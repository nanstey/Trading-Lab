from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import yaml


HyperliquidKind = Literal["perp", "outcome"]


@dataclass(frozen=True)
class PolymarketLeg:
    condition_id: str
    yes_token_id: str
    no_token_id: str


@dataclass(frozen=True)
class HyperliquidLeg:
    kind: HyperliquidKind
    network: str = "mainnet"
    symbol: str | None = None
    outcome_id: int | None = None
    side: int | None = None


@dataclass(frozen=True)
class CrossVenueSpec:
    slug: str
    venue: str
    polymarket: PolymarketLeg
    hyperliquid: HyperliquidLeg
    strategy_module: str | None = None
    strategy_class: str | None = None
    strategy_config_class: str | None = None
    source_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "venue": self.venue,
            "polymarket": asdict(self.polymarket),
            "hyperliquid": asdict(self.hyperliquid),
            "strategy_module": self.strategy_module,
            "strategy_class": self.strategy_class,
            "strategy_config_class": self.strategy_config_class,
            "source_path": self.source_path,
        }


def _read_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    return yaml.load(text[4:end].strip(), Loader=yaml.BaseLoader) or {}



def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value)



def _maybe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(str(value))



def load_cross_venue_spec(path: str | Path) -> CrossVenueSpec:
    md_path = Path(path)
    data = _read_frontmatter(md_path)
    cross = data.get("cross_venue") or {}
    pm = cross.get("polymarket") or {}
    hl = cross.get("hyperliquid") or {}
    hl_kind_raw = _stringify(hl.get("kind")) or "perp"
    hl_kind: HyperliquidKind = "outcome" if hl_kind_raw == "outcome" else "perp"
    return CrossVenueSpec(
        slug=_stringify(data.get("slug")),
        venue=_stringify(data.get("venue")),
        polymarket=PolymarketLeg(
            condition_id=_stringify(pm.get("condition_id")),
            yes_token_id=_stringify(pm.get("yes_token_id")),
            no_token_id=_stringify(pm.get("no_token_id")),
        ),
        hyperliquid=HyperliquidLeg(
            kind=hl_kind,
            network=_stringify(hl.get("network")) or "mainnet",
            symbol=_stringify(hl.get("symbol")) or None,
            outcome_id=_maybe_int(hl.get("outcome_id")),
            side=_maybe_int(hl.get("side")),
        ),
        strategy_module=_stringify(data.get("strategy_module")) or None,
        strategy_class=_stringify(data.get("strategy_class")) or None,
        strategy_config_class=_stringify(data.get("strategy_config_class")) or None,
        source_path=str(md_path),
    )



def validate_cross_venue_spec(spec: CrossVenueSpec) -> list[str]:
    errors: list[str] = []
    if not spec.slug:
        errors.append("slug is required")
    if spec.venue != "cross_venue":
        errors.append("venue must be 'cross_venue'")
    if not spec.polymarket.condition_id:
        errors.append("cross_venue.polymarket.condition_id is required")
    if not spec.polymarket.yes_token_id:
        errors.append("cross_venue.polymarket.yes_token_id is required")
    if not spec.polymarket.no_token_id:
        errors.append("cross_venue.polymarket.no_token_id is required")
    if spec.hyperliquid.kind not in {"perp", "outcome"}:
        errors.append("cross_venue.hyperliquid.kind must be 'perp' or 'outcome'")
    if spec.hyperliquid.kind == "perp" and not spec.hyperliquid.symbol:
        errors.append("cross_venue.hyperliquid.symbol is required when kind=perp")
    if spec.hyperliquid.kind == "outcome":
        if spec.hyperliquid.outcome_id is None:
            errors.append("cross_venue.hyperliquid.outcome_id is required when kind=outcome")
        if spec.hyperliquid.side not in (0, 1):
            errors.append("cross_venue.hyperliquid.side must be 0 or 1 when kind=outcome")
    return errors
