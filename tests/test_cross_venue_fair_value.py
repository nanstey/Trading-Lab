from __future__ import annotations

from decimal import Decimal

from trading_lab.research.cross_venue_fair_value import AnchoredFairValueModel


def test_anchored_fair_value_is_half_at_anchor() -> None:
    model = AnchoredFairValueModel(anchor_price=Decimal("65000"), scale=Decimal("2500"))

    fair_value = model.probability(Decimal("65000"))

    assert fair_value == Decimal("0.5")


def test_anchored_fair_value_monotonic_and_bounded() -> None:
    model = AnchoredFairValueModel(anchor_price=Decimal("65000"), scale=Decimal("2500"))

    low = model.probability(Decimal("60000"))
    mid = model.probability(Decimal("65000"))
    high = model.probability(Decimal("70000"))

    assert Decimal("0.01") <= low < mid < high <= Decimal("0.99")


def test_anchored_fair_value_bias_shifts_center() -> None:
    neutral = AnchoredFairValueModel(anchor_price=Decimal("65000"), scale=Decimal("2500"))
    biased = AnchoredFairValueModel(anchor_price=Decimal("65000"), scale=Decimal("2500"), bias=Decimal("0.4"))

    assert neutral.probability(Decimal("65000")) == Decimal("0.5")
    assert biased.probability(Decimal("65000")) > Decimal("0.5")
