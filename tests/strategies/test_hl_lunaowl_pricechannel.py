"""Unit tests for Hyperliquid LunaOwl PriceChannel clone logic."""

from __future__ import annotations

import math

from trading_lab.strategies.hl_lunaowl_pricechannel import (
    PriceChannelSnapshot,
    compute_price_channel,
    decide_pricechannel_action,
)


def test_compute_price_channel_requires_warmup_history() -> None:
    snapshot = compute_price_channel(
        highs=[100.0, 101.0, 102.0],
        lows=[95.0, 96.0, 97.0],
        channel_length=3,
    )
    assert snapshot is None


def test_compute_price_channel_uses_prior_bars_only() -> None:
    snapshot = compute_price_channel(
        highs=[100.0, 102.0, 104.0, 110.0],
        lows=[90.0, 92.0, 94.0, 80.0],
        channel_length=3,
    )
    assert snapshot is not None
    assert math.isclose(snapshot.upper, 104.0)
    assert math.isclose(snapshot.lower, 90.0)
    assert math.isclose(snapshot.midpoint, 97.0)
    assert snapshot.gap_state == "gapUp"


def test_decide_pricechannel_action_enters_long_on_close_breakout() -> None:
    action = decide_pricechannel_action(
        close=105.0,
        snapshot=PriceChannelSnapshot(upper=104.0, lower=90.0, midpoint=97.0, gap_state="gapUp"),
        position_side="FLAT",
        allow_short=True,
        exit_on_midline_reentry=False,
    )
    assert action == "ENTER_LONG"


def test_decide_pricechannel_action_enters_short_when_allowed() -> None:
    action = decide_pricechannel_action(
        close=89.0,
        snapshot=PriceChannelSnapshot(upper=104.0, lower=90.0, midpoint=97.0, gap_state="gapUp"),
        position_side="FLAT",
        allow_short=True,
        exit_on_midline_reentry=False,
    )
    assert action == "ENTER_SHORT"


def test_decide_pricechannel_action_respects_long_only_mode() -> None:
    action = decide_pricechannel_action(
        close=89.0,
        snapshot=PriceChannelSnapshot(upper=104.0, lower=90.0, midpoint=97.0, gap_state="gapUp"),
        position_side="FLAT",
        allow_short=False,
        exit_on_midline_reentry=False,
    )
    assert action == "HOLD"


def test_decide_pricechannel_action_flips_short_from_long() -> None:
    action = decide_pricechannel_action(
        close=89.0,
        snapshot=PriceChannelSnapshot(upper=104.0, lower=90.0, midpoint=97.0, gap_state="gapUp"),
        position_side="LONG",
        allow_short=True,
        exit_on_midline_reentry=False,
    )
    assert action == "FLIP_SHORT"


def test_decide_pricechannel_action_can_exit_on_midline_reentry() -> None:
    action = decide_pricechannel_action(
        close=96.0,
        snapshot=PriceChannelSnapshot(upper=104.0, lower=90.0, midpoint=97.0, gap_state="gapUp"),
        position_side="LONG",
        allow_short=True,
        exit_on_midline_reentry=True,
    )
    assert action == "EXIT"
