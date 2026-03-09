//! polyfill-rs: Ultra-low latency WebSocket client for Polymarket order routing.
//!
//! ## Purpose
//!
//! This crate provides a zero-allocation hot path for the cancel/replace loop
//! that is critical for competitive market making on the Polymarket CLOB.
//!
//! ## Performance targets
//!
//! - Cancel/replace round-trip: < 100ms from signal to confirmation
//! - Zero heap allocations on the hot path (message construction uses stack buffers)
//! - Branchless integer arithmetic for price calculations
//! - Single-threaded tokio runtime for minimal context switching
//!
//! ## Architecture
//!
//! ```text
//! Python strategy (NautilusTrader)
//!     │
//!     │  PyO3 FFI (optional feature: python-bindings)
//!     ▼
//! polyfill-rs (Rust)
//!     │
//!     │  tokio-tungstenite WebSocket
//!     ▼
//! Polymarket CLOB WS endpoint
//! ```
//!
//! ## Python/Rust boundary
//!
//! When compiled with the `python-bindings` feature, this crate exposes
//! a Python extension module via PyO3. The Python strategy calls into Rust
//! only for the latency-critical cancel/replace path; all strategy logic
//! remains in Python where it is easier to iterate on.
//!
//! ## Usage (Rust)
//!
//! ```rust,no_run
//! use polyfill_rs::ws_client::PolymarketWsClient;
//!
//! #[tokio::main]
//! async fn main() {
//!     let mut client = PolymarketWsClient::new("wss://ws-subscriptions-clob.polymarket.com/ws/");
//!     client.connect().await.expect("Failed to connect");
//!     client.subscribe_market("0xabc123...").await;
//! }
//! ```

pub mod ws_client;

#[cfg(feature = "python-bindings")]
mod python {
    use pyo3::prelude::*;
    use super::ws_client::PolymarketWsClient;

    /// Python-callable wrapper around PolymarketWsClient.
    ///
    /// Exposes the cancel/replace hot path to Python strategies via PyO3.
    /// All Python calls block on a dedicated tokio runtime to avoid
    /// interference with the Python async event loop.
    #[pyclass]
    pub struct PyPolymarketWsClient {
        inner: PolymarketWsClient,
        runtime: tokio::runtime::Runtime,
    }

    #[pymethods]
    impl PyPolymarketWsClient {
        #[new]
        fn new(url: &str) -> Self {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .expect("Failed to build tokio runtime");
            Self {
                inner: PolymarketWsClient::new(url),
                runtime,
            }
        }

        /// Connect to the Polymarket WebSocket endpoint.
        fn connect(&mut self) -> PyResult<()> {
            self.runtime
                .block_on(self.inner.connect())
                .map_err(|e| pyo3::exceptions::PyConnectionError::new_err(e.to_string()))
        }

        /// Subscribe to order book updates for a market token.
        fn subscribe_market(&mut self, token_id: &str) -> PyResult<()> {
            self.runtime.block_on(self.inner.subscribe_market(token_id));
            Ok(())
        }

        /// Cancel an existing order and replace with a new one (hot path).
        ///
        /// This is the latency-critical path. Target: < 100ms round-trip.
        ///
        /// Parameters
        /// ----------
        /// cancel_id : str
        ///     Order ID to cancel.
        /// new_order_json : str
        ///     JSON-encoded replacement order payload.
        ///
        /// Returns
        /// -------
        /// str
        ///     JSON response from the venue.
        fn cancel_replace(
            &mut self,
            cancel_id: &str,
            new_order_json: &str,
        ) -> PyResult<String> {
            self.runtime
                .block_on(self.inner.cancel_replace(cancel_id, new_order_json))
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        }
    }

    /// Register the polyfill_rs Python module.
    #[pymodule]
    fn polyfill_rs(_py: Python<'_>, m: &PyModule) -> PyResult<()> {
        m.add_class::<PyPolymarketWsClient>()?;
        Ok(())
    }
}
