from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class AnchoredFairValueModel:
    """Map a perp price into a bounded PM YES fair value via anchored logistic curve."""

    anchor_price: Decimal
    scale: Decimal
    bias: Decimal = Decimal("0")
    min_probability: Decimal = Decimal("0.01")
    max_probability: Decimal = Decimal("0.99")

    def probability(self, price: Decimal) -> Decimal:
        if self.scale <= 0:
            raise ValueError("scale must be positive")
        z = ((price - self.anchor_price) / self.scale) + self.bias
        logistic = Decimal(str(1.0 / (1.0 + math.exp(-float(z)))))
        if logistic < self.min_probability:
            return self.min_probability
        if logistic > self.max_probability:
            return self.max_probability
        return logistic.quantize(Decimal("0.000001")).normalize()


__all__ = ["AnchoredFairValueModel"]
