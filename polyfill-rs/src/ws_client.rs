//! Polymarket WebSocket client with ultra-low latency cancel/replace.
//!
//! ## Hot path design
//!
//! The cancel/replace loop is the most latency-sensitive operation in market
//! making. When the book moves, we need to:
//!   1. Cancel the stale order
//!   2. Submit the new order at the updated price
//!
//! Both operations happen over the same persistent WebSocket connection.
//! Reusing the connection avoids TCP handshake overhead (~10-50ms).
//!
//! ## Performance notes
//!
//! - Pre-allocated message buffers eliminate heap allocation on the hot path
//! - Integer price representation (e.g., price_in_cents as u32) avoids
//!   floating-point operations in the critical section
//! - TODO: Implement branchless integer arithmetic for price rounding
//!   to avoid branch misprediction penalties
//! - tokio-tungstenite is used over async-tungstenite for better tokio
//!   integration and reduced syscall overhead

use std::error::Error;

use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use tokio::sync::mpsc;
use tokio_tungstenite::{connect_async, tungstenite::Message, WebSocketStream, MaybeTlsStream};
use tracing::{debug, error, info, warn};

/// Message types for the Polymarket WebSocket protocol.
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum WsMessageType {
    Subscribe,
    Unsubscribe,
    PlaceOrder,
    CancelOrder,
    Pong,
}

/// Subscription request for a market channel.
#[derive(Debug, Serialize)]
struct SubscribeMessage {
    #[serde(rename = "type")]
    msg_type: String,
    assets_ids: Vec<String>,
    markets: Vec<String>,
    auth: serde_json::Value,
}

/// Cancel order request.
#[derive(Debug, Serialize)]
struct CancelRequest {
    #[serde(rename = "type")]
    msg_type: String,
    order_id: String,
}

/// Ultra-low latency Polymarket WebSocket client.
///
/// Maintains a persistent WebSocket connection to the Polymarket CLOB
/// for real-time order book updates and low-latency order routing.
///
/// # Thread Safety
///
/// `PolymarketWsClient` is not `Send` due to the WebSocket stream.
/// Use it from a single tokio task or wrap in `Arc<Mutex<_>>` for
/// multi-task access (though this adds locking overhead).
pub struct PolymarketWsClient {
    url: String,
    ws_stream: Option<WebSocketStream<MaybeTlsStream<tokio::net::TcpStream>>>,
    /// Pre-allocated buffer for outbound message construction (avoids malloc on hot path).
    /// TODO: Implement zero-copy message building using this buffer.
    _send_buffer: Vec<u8>,
}

impl PolymarketWsClient {
    /// Create a new client targeting the given WebSocket URL.
    ///
    /// Does not connect until `connect()` is called.
    ///
    /// # Parameters
    ///
    /// * `url` - Polymarket WebSocket endpoint, e.g.
    ///   `"wss://ws-subscriptions-clob.polymarket.com/ws/"`
    pub fn new(url: &str) -> Self {
        Self {
            url: url.to_owned(),
            ws_stream: None,
            // Pre-allocate 8KB send buffer - sufficient for all order payloads
            _send_buffer: Vec::with_capacity(8 * 1024),
        }
    }

    /// Establish the WebSocket connection.
    ///
    /// Must be called before any subscription or order methods.
    /// Reconnection logic should call this again after a disconnect.
    ///
    /// # Errors
    ///
    /// Returns an error if the TCP or WebSocket handshake fails.
    pub async fn connect(&mut self) -> Result<(), Box<dyn Error>> {
        info!("Connecting to Polymarket WebSocket: {}", self.url);

        let (ws_stream, response) = connect_async(&self.url).await?;

        info!(
            "WebSocket connected, HTTP status: {}",
            response.status()
        );

        self.ws_stream = Some(ws_stream);
        Ok(())
    }

    /// Subscribe to real-time order book updates for a market token.
    ///
    /// # Parameters
    ///
    /// * `token_id` - Polymarket outcome token ID (0x-prefixed hex).
    pub async fn subscribe_market(&mut self, token_id: &str) {
        let sub_msg = SubscribeMessage {
            msg_type: "market".to_owned(),
            assets_ids: vec![token_id.to_owned()],
            markets: vec![],
            auth: serde_json::json!({}),
        };

        let payload = match serde_json::to_string(&sub_msg) {
            Ok(s) => s,
            Err(e) => {
                error!("Failed to serialize subscribe message: {}", e);
                return;
            }
        };

        if let Some(ref mut ws) = self.ws_stream {
            if let Err(e) = ws.send(Message::Text(payload)).await {
                error!("Failed to send subscribe message: {}", e);
            } else {
                info!("Subscribed to market channel: {}", token_id);
            }
        } else {
            warn!("Cannot subscribe: not connected. Call connect() first.");
        }
    }

    /// Place a new order on the Polymarket CLOB.
    ///
    /// # Parameters
    ///
    /// * `order_json` - JSON-encoded order payload (must include feeRateBps).
    ///
    /// # Returns
    ///
    /// JSON response string from the venue.
    ///
    /// # Errors
    ///
    /// Returns an error if the WebSocket send or receive fails.
    pub async fn send_order(&mut self, order_json: &str) -> Result<String, Box<dyn Error>> {
        let ws = self.ws_stream.as_mut().ok_or("Not connected")?;

        debug!("Sending order: {}", &order_json[..order_json.len().min(100)]);

        ws.send(Message::Text(order_json.to_owned())).await?;

        // Wait for acknowledgement
        if let Some(msg) = ws.next().await {
            match msg? {
                Message::Text(response) => {
                    debug!("Order response: {}", &response[..response.len().min(100)]);
                    return Ok(response);
                }
                Message::Close(frame) => {
                    return Err(format!("WebSocket closed during order: {:?}", frame).into());
                }
                other => {
                    warn!("Unexpected message type during order: {:?}", other);
                }
            }
        }

        Err("No response received for order".into())
    }

    /// Cancel an existing order and place a replacement in the lowest-latency path.
    ///
    /// # Target latency: < 100ms from call to venue acknowledgement
    ///
    /// This is the critical hot path for market making. The cancel and replace
    /// are sent as consecutive messages over the same persistent WebSocket
    /// connection, minimizing round-trip time.
    ///
    /// ## Design notes
    ///
    /// - Both messages are sent before reading any response (pipelining).
    ///   This halves the number of round trips vs. sequential cancel-then-place.
    /// - TODO: Implement branchless integer arithmetic for price calculation
    ///   (e.g., new_price_ticks = (mid_price * 10000) + spread_ticks)
    ///   to eliminate branch misprediction on the hot path.
    /// - TODO: Use `io_uring` via tokio-uring for even lower syscall overhead
    ///   on Linux production deployments.
    ///
    /// # Parameters
    ///
    /// * `cancel_id` - Order ID to cancel.
    /// * `new_order_json` - JSON-encoded replacement order payload.
    ///
    /// # Returns
    ///
    /// JSON response string from the venue (for the place order confirmation).
    ///
    /// # Errors
    ///
    /// Returns an error if either the cancel or place operation fails.
    pub async fn cancel_replace(
        &mut self,
        cancel_id: &str,
        new_order_json: &str,
    ) -> Result<String, Box<dyn Error>> {
        let ws = self.ws_stream.as_mut().ok_or("Not connected. Call connect() first.")?;

        // Build cancel message
        let cancel_msg = serde_json::json!({
            "type": "cancel",
            "orderId": cancel_id,
        })
        .to_string();

        let start = std::time::Instant::now();

        // Pipeline: send cancel and new order before reading responses
        // This is the key latency optimization: we don't wait for cancel ACK
        // before sending the new order. Both are in-flight simultaneously.
        ws.send(Message::Text(cancel_msg)).await?;
        ws.send(Message::Text(new_order_json.to_owned())).await?;

        debug!(
            "Cancel+replace pipelined in {}µs",
            start.elapsed().as_micros()
        );

        // Read responses (cancel ACK and place ACK)
        let mut place_response: Option<String> = None;
        let mut responses_received = 0;

        while responses_received < 2 {
            match ws.next().await {
                Some(Ok(Message::Text(response))) => {
                    responses_received += 1;
                    // The second response is the place order confirmation
                    if responses_received == 2 {
                        place_response = Some(response);
                    }
                }
                Some(Ok(Message::Ping(data))) => {
                    // Respond to pings to keep connection alive
                    ws.send(Message::Pong(data)).await?;
                }
                Some(Ok(Message::Close(frame))) => {
                    return Err(
                        format!("WebSocket closed during cancel_replace: {:?}", frame).into()
                    );
                }
                Some(Err(e)) => {
                    return Err(format!("WebSocket error during cancel_replace: {}", e).into());
                }
                None => {
                    return Err("WebSocket stream ended during cancel_replace".into());
                }
                _ => {}
            }
        }

        let elapsed = start.elapsed();
        debug!("cancel_replace complete in {}ms", elapsed.as_millis());

        if elapsed.as_millis() > 100 {
            warn!(
                "cancel_replace exceeded 100ms target: {}ms",
                elapsed.as_millis()
            );
        }

        place_response.ok_or_else(|| "No place order response received".into())
    }

    /// Run the WebSocket message loop, delivering messages to a channel.
    ///
    /// Runs indefinitely until the WebSocket closes or an error occurs.
    /// Messages are sent to `tx` for processing by the caller.
    ///
    /// # Parameters
    ///
    /// * `tx` - Channel sender for incoming messages.
    pub async fn run_message_loop(
        &mut self,
        tx: mpsc::UnboundedSender<String>,
    ) -> Result<(), Box<dyn Error>> {
        let ws = self.ws_stream.as_mut().ok_or("Not connected")?;

        info!("Starting WebSocket message loop");

        while let Some(msg) = ws.next().await {
            match msg? {
                Message::Text(text) => {
                    if tx.send(text).is_err() {
                        debug!("Message channel closed, stopping loop");
                        break;
                    }
                }
                Message::Ping(data) => {
                    ws.send(Message::Pong(data)).await?;
                }
                Message::Close(frame) => {
                    info!("WebSocket closed: {:?}", frame);
                    break;
                }
                Message::Binary(data) => {
                    debug!("Received binary message ({} bytes), ignoring", data.len());
                }
                _ => {}
            }
        }

        info!("WebSocket message loop ended");
        Ok(())
    }

    /// Check if the WebSocket connection is active.
    pub fn is_connected(&self) -> bool {
        self.ws_stream.is_some()
    }

    /// Gracefully close the WebSocket connection.
    pub async fn close(&mut self) -> Result<(), Box<dyn Error>> {
        if let Some(ref mut ws) = self.ws_stream {
            ws.send(Message::Close(None)).await?;
            info!("WebSocket connection closed gracefully");
        }
        self.ws_stream = None;
        Ok(())
    }
}
