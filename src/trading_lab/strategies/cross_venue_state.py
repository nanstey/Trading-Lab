from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CrossVenueLeggingStateMachine:
    state: str = "flat"
    direction: str = ""
    needs_polymarket_flatten: bool = False
    last_reason: str = ""

    def start_entry(self, *, direction: str) -> None:
        if self.state != "flat":
            raise RuntimeError(f"cannot start entry from state={self.state}")
        self.state = "entering_pm"
        self.direction = direction
        self.needs_polymarket_flatten = False
        self.last_reason = ""

    def on_polymarket_fill(self) -> None:
        if self.state != "entering_pm":
            raise RuntimeError(f"unexpected polymarket fill in state={self.state}")
        self.state = "entering_hl_hedge"

    def on_hyperliquid_fill(self) -> None:
        if self.state != "entering_hl_hedge":
            raise RuntimeError(f"unexpected hyperliquid fill in state={self.state}")
        self.state = "hedged"
        self.needs_polymarket_flatten = False

    def on_hyperliquid_reject(self, *, reason: str) -> None:
        if self.state != "entering_hl_hedge":
            raise RuntimeError(f"unexpected hyperliquid reject in state={self.state}")
        self.state = "halted"
        self.needs_polymarket_flatten = True
        self.last_reason = reason

    def on_polymarket_flattened(self) -> None:
        if not self.needs_polymarket_flatten:
            raise RuntimeError("flatten requested with no outstanding exposure")
        self.state = "flat"
        self.direction = ""
        self.needs_polymarket_flatten = False


__all__ = ["CrossVenueLeggingStateMachine"]
