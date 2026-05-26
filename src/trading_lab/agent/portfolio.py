"""
Per-strategy capital allocator — a pre-trade cap gate on top of NT's Portfolio.

NautilusTrader's `Portfolio` already tracks per-instrument / per-venue net
exposure, realised + unrealised PnL, and equity (see
https://nautilustrader.io/docs/latest/concepts/portfolio/). What it does
NOT provide is:

  1. A per-strategy capital cap.
  2. A pre-trade gate that rejects orders that would push past that cap.

This module fills both gaps. The allocator is intentionally thin:

  - State of record for "what's currently deployed" is NT's `Portfolio`
    (single source of truth — no double-bookkeeping with risk of drift).
  - The allocator holds only the static per-slug cap spec and a reference
    to the live Portfolio.
  - `check_order(order)` reads current USDC exposure from Portfolio,
    adds the proposed order notional, and accepts/rejects against the cap.

Cap specifications (in `config/portfolio.yaml` `allocations:` map):

  Absolute USDC:        `tick-mean-revert: 400.0`           (or 400 as int)
  Percent of equity:    `tick-mean-revert: "40%"`           (string with %)
                        `tick-mean-revert: 0.4`             (float 0 < x < 1)

When a slug's cap is percentage-based the resolved cap is
`pct * venue_equity_provider.current_usdc()` and is re-read on every
`check_order` call — so as the venue equity grows or shrinks the cap
adapts automatically. Absolute caps are constant for the process lifetime.

Architectural assumption: one strategy per TradingNode process. Each
runner builds a TradingNode whose Portfolio aggregates exposure for that
one strategy only, so `portfolio.net_exposures(venue)` is effectively the
per-strategy exposure. The equity provider, by contrast, reads the
WHOLE venue wallet — so percentages compose correctly across processes.

Not in v1 (deferred — design notes for when needed):

  - Cross-process exposure coordination. Each process knows its own NT
    Portfolio; we don't poll other processes' state. The pct-of-equity
    spec works around this: if all three strategies say "I want 40% of
    equity", their sum is capped at 120% but only enforced at the venue
    by the equity itself (the fourth attempt to deploy capital hits a
    wallet-balance failure at order time). `validate_allocations` warns
    when pct entries sum > 1.0.
  - Per-instrument concentration limits. The allocator caps total
    deployed capital per strategy; if a strategy wants 100% in one
    instrument that's allowed.
  - Time-based equity refresh. The runner primes the equity provider at
    startup; refreshing periodically (e.g. every 5 minutes) would let
    caps adjust mid-run as positions resolve. v1 leaves refresh as a
    manual op (`scripts/portfolio_status.py` triggers a refresh).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class AllocatorDecision:
    """Returned by `check_order` — accept + diagnostics for events log."""

    accepted: bool
    reason: str = ""
    proposed_notional_usdc: float = 0.0
    open_notional_before: float = 0.0
    open_notional_after: float = 0.0
    cap_usdc: float = 0.0


# ---------------------------------------------------------------------------
# Cap spec parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapSpec:
    """
    Parsed cap specification.

    Exactly one of `absolute_usdc` or `pct_of_equity` is set. Pct is in [0, 1].
    """

    absolute_usdc: float | None = None
    pct_of_equity: float | None = None

    @property
    def is_pct(self) -> bool:
        return self.pct_of_equity is not None

    def __post_init__(self) -> None:
        if (self.absolute_usdc is None) == (self.pct_of_equity is None):
            raise ValueError("CapSpec must set exactly one of absolute_usdc / pct_of_equity")
        if self.pct_of_equity is not None and not (0 < self.pct_of_equity <= 1.0):
            raise ValueError(
                f"pct_of_equity must be in (0, 1], got {self.pct_of_equity}"
            )
        if self.absolute_usdc is not None and self.absolute_usdc <= 0:
            raise ValueError(f"absolute_usdc must be > 0, got {self.absolute_usdc}")

    def describe(self) -> str:
        if self.is_pct:
            return f"{self.pct_of_equity * 100:.2f}% of venue equity"
        return f"${self.absolute_usdc:.2f}"


def parse_cap(spec) -> CapSpec:
    """
    Parse a raw config value into a `CapSpec`.

    Rules:
      - int or float > 1     → absolute USDC
      - int or float in (0, 1] → percentage (0.4 means 40%)
      - str ending in "%"    → percentage (`"40%"` → 0.4)
      - str without %        → tried as float and dispatched as above
    """
    if isinstance(spec, str):
        s = spec.strip()
        if s.endswith("%"):
            val = float(s[:-1]) / 100.0
            return CapSpec(pct_of_equity=val)
        spec = float(s)

    if isinstance(spec, bool):
        raise ValueError(f"bool is not a valid cap spec: {spec!r}")

    if isinstance(spec, int | float):
        val = float(spec)
        if 0 < val <= 1.0:
            return CapSpec(pct_of_equity=val)
        return CapSpec(absolute_usdc=val)

    raise ValueError(f"unrecognised cap spec: {spec!r} (type {type(spec).__name__})")


# ---------------------------------------------------------------------------
# Allocator
# ---------------------------------------------------------------------------


class PortfolioAllocator:
    """
    Per-strategy USDC cap enforcement, backed by NT's `Portfolio`.

    Lifecycle:
      1. Runner constructs via `PortfolioAllocator.for_slug(slug, cfg, equity_provider)`.
      2. After `node.build()` the runner calls `set_portfolio(node.portfolio)`
         so the allocator can read live exposure.
      3. Execution client calls `check_order(order)` before submitting.
         If rejected, it emits `OrderRejected` and a `portfolio_alloc_breach`
         event; the order never reaches the venue (live) or the paper
         fill engine (paper).

    Cap can be absolute USDC or pct-of-equity. With a pct cap the
    allocator queries the equity provider on every `check_order`, so
    caps grow/shrink with the wallet automatically.
    """

    def __init__(
        self,
        slug: str,
        cap: CapSpec,
        equity_provider=None,
        venue: str = "POLYMARKET",
    ) -> None:
        if cap.is_pct and equity_provider is None:
            raise ValueError(
                f"slug={slug}: pct cap requires an equity_provider"
            )
        self._slug = slug
        self._cap_spec = cap
        self._equity = equity_provider
        self._venue = venue
        self._portfolio = None  # set by runner via set_portfolio()

    # ------------------------------------------------------------------
    # Wiring (called once by the runner after node.build())
    # ------------------------------------------------------------------

    def set_portfolio(self, portfolio) -> None:
        """Attach NT's Portfolio (`node.portfolio` or `node.kernel.portfolio`)."""
        self._portfolio = portfolio

    def set_equity_provider(self, equity_provider) -> None:
        self._equity = equity_provider

    # ------------------------------------------------------------------
    # Read accessors
    # ------------------------------------------------------------------

    @property
    def slug(self) -> str:
        return self._slug

    @property
    def cap_spec(self) -> CapSpec:
        return self._cap_spec

    @property
    def cap_usdc(self) -> float:
        """
        Resolved cap in USDC at the current moment.

        For absolute caps: constant. For pct caps: `pct * current_equity`,
        re-read each call.
        """
        if self._cap_spec.is_pct:
            if self._equity is None:
                return 0.0
            equity = float(self._equity.current_usdc())
            return max(0.0, self._cap_spec.pct_of_equity * equity)
        return float(self._cap_spec.absolute_usdc)

    @property
    def open_notional_usdc(self) -> float:
        return self._current_exposure_usdc()

    @property
    def available_usdc(self) -> float:
        return max(0.0, self.cap_usdc - self._current_exposure_usdc())

    @property
    def utilisation_pct(self) -> float:
        cap = self.cap_usdc
        if cap <= 0:
            return 0.0
        return 100.0 * self._current_exposure_usdc() / cap

    def snapshot(self) -> dict:
        """JSON-friendly summary for events / status reports."""
        cap = self.cap_usdc
        open_n = self._current_exposure_usdc()
        equity = float(self._equity.current_usdc()) if self._equity is not None else None
        return {
            "slug": self._slug,
            "venue": self._venue,
            "cap_spec": self._cap_spec.describe(),
            "is_pct": self._cap_spec.is_pct,
            "cap_usdc": round(cap, 4),
            "venue_equity_usdc": round(equity, 4) if equity is not None else None,
            "open_notional_usdc": round(open_n, 4),
            "available_usdc": round(max(0.0, cap - open_n), 4),
            "utilisation_pct": round(
                100.0 * open_n / cap if cap > 0 else 0.0, 2
            ),
            "portfolio_attached": self._portfolio is not None,
        }

    # ------------------------------------------------------------------
    # Pre-trade gate — called by the execution client
    # ------------------------------------------------------------------

    def check_order(self, order) -> AllocatorDecision:
        """
        Accept/reject `order` based on whether it would push us over cap.

        SELLs that reduce exposure (close an existing position) are
        ALWAYS accepted — no cap check. SELLs that increase exposure
        (naked short) are treated like BUYs. We detect via the
        instrument's net position in NT Portfolio.
        """
        try:
            from nautilus_trader.model.enums import OrderSide

            side_is_buy = order.side == OrderSide.BUY
        except Exception:
            side_is_buy = str(getattr(order, "side", "")).upper().endswith("BUY")

        try:
            qty = float(order.quantity)
            price = float(order.price)
        except Exception as exc:
            log.warning("allocator: cannot read qty/price from order: %s", exc)
            return AllocatorDecision(
                accepted=True,
                reason="cannot evaluate (missing qty/price)",
                cap_usdc=self.cap_usdc,
            )

        proposed = abs(qty * price)
        before = self._current_exposure_usdc()
        cap = self.cap_usdc

        if not side_is_buy:
            iid = getattr(order, "instrument_id", None)
            if iid is not None and self._has_long_position(iid):
                return AllocatorDecision(
                    accepted=True,
                    reason="closing position",
                    proposed_notional_usdc=proposed,
                    open_notional_before=before,
                    open_notional_after=max(0.0, before - proposed),
                    cap_usdc=cap,
                )

        after = before + proposed
        if cap <= 0:
            return AllocatorDecision(
                accepted=False,
                reason=(
                    f"cap unresolved ({self._cap_spec.describe()}): "
                    f"equity provider returned 0 — refresh and retry"
                ),
                proposed_notional_usdc=proposed,
                open_notional_before=before,
                open_notional_after=after,
                cap_usdc=cap,
            )
        if after > cap:
            return AllocatorDecision(
                accepted=False,
                reason=(
                    f"cap exceeded: ${after:.2f} > ${cap:.2f} "
                    f"({self._cap_spec.describe()}; "
                    f"open=${before:.2f} + new=${proposed:.2f})"
                ),
                proposed_notional_usdc=proposed,
                open_notional_before=before,
                open_notional_after=after,
                cap_usdc=cap,
            )
        return AllocatorDecision(
            accepted=True,
            reason="under cap",
            proposed_notional_usdc=proposed,
            open_notional_before=before,
            open_notional_after=after,
            cap_usdc=cap,
        )

    # ------------------------------------------------------------------
    # Internals — exposure extraction from NT Portfolio
    # ------------------------------------------------------------------

    def _current_exposure_usdc(self) -> float:
        if self._portfolio is None:
            return 0.0
        try:
            from nautilus_trader.model.identifiers import Venue

            exposures = self._portfolio.net_exposures(Venue(self._venue))
        except Exception as exc:
            log.debug("allocator: net_exposures(%s) failed: %s", self._venue, exc)
            return 0.0
        if not exposures:
            return 0.0
        total = 0.0
        for currency, money in exposures.items():
            cur_name = getattr(currency, "code", None) or str(currency)
            if cur_name.upper().startswith("USDC"):
                try:
                    total += float(money)
                except Exception:
                    try:
                        total += float(money.as_double())
                    except Exception:
                        pass
        return abs(total)

    def _has_long_position(self, instrument_id) -> bool:
        if self._portfolio is None or instrument_id is None:
            return False
        try:
            net = self._portfolio.net_position(instrument_id)
            return float(net) > 0
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def for_slug(slug: str, cfg, equity_provider=None) -> PortfolioAllocator:
    """
    Build a `PortfolioAllocator` for `slug` from a loaded TradingConfig.

    Cap resolution:
      1. `cfg.portfolio.allocations[slug]` (pct or absolute) if present
      2. Fall back to fair-share over active PAPER+LIVE hypothesis count.
         Uses absolute USDC (`max_total_exposure_usdc / active_count`) —
         not pct — so the fallback is deterministic without an equity provider.
      3. Final fallback: legacy per-strategy `risk.max_position_usdc`.
    """
    allocations = cfg.portfolio.allocations or {}
    if slug in allocations:
        spec = parse_cap(allocations[slug])
        log.info("allocator slug=%s cap=%s (explicit)", slug, spec.describe())
        return PortfolioAllocator(
            slug=slug, cap=spec, equity_provider=equity_provider,
        )

    try:
        from trading_lab.agent import lifecycle

        active = sum(
            len(lifecycle.list_hypotheses(state=s))
            for s in (lifecycle.State.PAPER.value, lifecycle.State.LIVE.value)
        )
    except Exception:
        active = 1
    active = max(active, 1)

    total = float(cfg.portfolio.risk.max_total_exposure_usdc or 0)
    if total > 0:
        cap = total / active
        log.warning(
            "allocator slug=%s no explicit allocation; using fair-share "
            "$%.2f = $%.2f / %d active strategies",
            slug, cap, total, active,
        )
        return PortfolioAllocator(
            slug=slug, cap=CapSpec(absolute_usdc=cap),
            equity_provider=equity_provider,
        )

    cap = float(cfg.portfolio.risk.max_position_usdc or 100.0)
    log.warning(
        "allocator slug=%s no portfolio allocation found; using "
        "legacy max_position_usdc=$%.2f (over-allocates when multiple "
        "strategies share an account)",
        slug, cap,
    )
    return PortfolioAllocator(
        slug=slug, cap=CapSpec(absolute_usdc=cap),
        equity_provider=equity_provider,
    )


def validate_allocations(cfg) -> list[str]:
    """
    Return human-readable warnings about the configured allocation map.

    Checks:
      - sum(absolute entries) > max_total_exposure_usdc → over-allocation
      - sum(pct entries) > 1.0 → over-allocation
      - any entry < 0 or > 100% → invalid
    """
    warnings: list[str] = []
    allocations = cfg.portfolio.allocations or {}
    total_cap = float(cfg.portfolio.risk.max_total_exposure_usdc or 0)

    absolute_sum = 0.0
    pct_sum = 0.0
    for slug, raw in allocations.items():
        try:
            spec = parse_cap(raw)
        except ValueError as exc:
            warnings.append(f"allocations[{slug}] = {raw!r}: {exc}")
            continue
        if spec.is_pct:
            pct_sum += spec.pct_of_equity
        else:
            absolute_sum += spec.absolute_usdc

    if total_cap > 0 and absolute_sum > total_cap:
        warnings.append(
            f"sum of absolute per-slug allocations ${absolute_sum:.2f} > "
            f"max_total_exposure_usdc ${total_cap:.2f} — over-committed."
        )
    if pct_sum > 1.0:
        warnings.append(
            f"sum of pct per-slug allocations {pct_sum * 100:.1f}% > 100% — "
            f"strategies will compete for the same equity slice."
        )
    return warnings
