#!/usr/bin/env python3
"""
Portfolio status — what each active strategy is allowed to deploy.

Resolves the cap map in `config/portfolio.yaml` for every active PAPER +
LIVE hypothesis, supporting both absolute USDC caps and pct-of-equity
caps. For pct caps the resolved cap is `pct * venue_equity`; with
`--refresh` we pull live equity from Polymarket before computing.

Modes:
  - `--json` (default): JSON object suitable for piping to the operator
    agent or another script.
  - `--md`            : human-readable markdown table.
  - `--refresh`       : fetch fresh venue equity from Polymarket (else
                         resolves pct caps against 0 — useful to see the
                         raw config without hitting the network).

Emits a `portfolio_status` event into `logs/events.jsonl` (suppress with
`--no-event`). Severity = critical if over-allocated.

Exit codes:
  0 — OK
  2 — over-allocated (sum of caps > max_total_exposure_usdc, or sum of
       pct > 100%)
  3 — bad CLI / config error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_lab.agent import portfolio as alloc_mod  # noqa: E402
from trading_lab.agent.events import emit_event  # noqa: E402
from trading_lab.agent.lifecycle import State, list_hypotheses  # noqa: E402
from trading_lab.config import load_config  # noqa: E402

log = logging.getLogger("portfolio_status")


def _build_equity_provider(cfg, refresh: bool):
    from trading_lab.agent.venue_equity import PolymarketEquityProvider
    from trading_lab.venues.polymarket.auth import L2Credentials, derive_address
    from trading_lab.venues.polymarket.client import PolymarketRestClient

    pk = cfg.polymarket.private_key.get_secret_value()
    if not pk:
        return None
    try:
        wallet = derive_address(pk)
    except Exception:
        return None

    rest = None
    if cfg.polymarket.has_l2_credentials:
        try:
            creds = L2Credentials(
                api_key=cfg.polymarket.api_key,
                api_secret=cfg.polymarket.api_secret.get_secret_value(),
                api_passphrase=cfg.polymarket.api_passphrase.get_secret_value(),
            )
            rest = PolymarketRestClient(http_url=cfg.polymarket.host, creds=creds)
        except Exception:
            pass

    provider = PolymarketEquityProvider(wallet_address=wallet, rest_client=rest)
    if refresh:
        try:
            asyncio.run(provider.refresh())
        except Exception as exc:
            log.warning("equity refresh failed: %s", exc)
    return provider


def collect_status(refresh: bool) -> dict:
    cfg = load_config()
    explicit = dict(cfg.portfolio.allocations or {})
    total_envelope = float(cfg.portfolio.risk.max_total_exposure_usdc or 0)
    daily_loss = float(cfg.portfolio.risk.daily_loss_limit_usdc or 0)

    active = []
    for state in (State.PAPER.value, State.LIVE.value):
        for h in list_hypotheses(state=state):
            active.append({"slug": h.slug, "state": state})

    equity_provider = _build_equity_provider(cfg, refresh=refresh)
    venue_equity = (
        equity_provider.current_usdc() if equity_provider is not None else 0.0
    )
    equity_source = None
    equity_age_s = None
    if equity_provider is not None and equity_provider.snapshot is not None:
        equity_source = equity_provider.snapshot.source
        equity_age_s = equity_provider.age_seconds()

    fair_share = (
        total_envelope / max(len(active), 1) if total_envelope > 0 else 0.0
    )

    per_slug = []
    summed_caps = 0.0
    pct_sum = 0.0
    for entry in active:
        slug = entry["slug"]
        if slug in explicit:
            try:
                spec = alloc_mod.parse_cap(explicit[slug])
            except ValueError as exc:
                per_slug.append({
                    "slug": slug, "state": entry["state"],
                    "cap_spec": str(explicit[slug]),
                    "cap_usdc": 0.0, "source": f"invalid: {exc}",
                })
                continue
            if spec.is_pct:
                pct_sum += spec.pct_of_equity
                cap = spec.pct_of_equity * venue_equity
                source = "pct-of-equity"
            else:
                cap = spec.absolute_usdc
                source = "explicit"
            spec_str = spec.describe()
        elif fair_share > 0:
            cap = fair_share
            source = "fair-share"
            spec_str = f"${fair_share:.2f}"
        else:
            cap = float(cfg.portfolio.risk.max_position_usdc or 100.0)
            source = "legacy-max-position"
            spec_str = f"${cap:.2f}"
        summed_caps += cap
        per_slug.append({
            "slug": slug, "state": entry["state"],
            "cap_spec": spec_str,
            "cap_usdc": round(cap, 4),
            "source": source,
        })

    warnings = alloc_mod.validate_allocations(cfg)
    over_allocated = (
        (total_envelope > 0 and summed_caps > total_envelope)
        or pct_sum > 1.0
    )

    return {
        "max_total_exposure_usdc": total_envelope,
        "daily_loss_limit_usdc": daily_loss,
        "explicit_allocations": explicit,
        "active_count": len(active),
        "fair_share_usdc": round(fair_share, 4),
        "venue_equity_usdc": round(venue_equity, 4),
        "venue_equity_source": equity_source,
        "venue_equity_age_seconds": (
            round(equity_age_s, 1) if equity_age_s is not None else None
        ),
        "per_slug": per_slug,
        "summed_caps_usdc": round(summed_caps, 4),
        "pct_sum": round(pct_sum, 4),
        "headroom_usdc": round(total_envelope - summed_caps, 4),
        "over_allocated": over_allocated,
        "warnings": warnings,
    }


def render_md(status: dict) -> str:
    lines: list[str] = []
    lines.append("# Portfolio status")
    lines.append("")
    lines.append(
        f"- Envelope (max_total_exposure_usdc): "
        f"**${status['max_total_exposure_usdc']:.2f}**"
    )
    lines.append(
        f"- Daily loss limit: ${status['daily_loss_limit_usdc']:.2f}"
    )
    if status["venue_equity_source"]:
        lines.append(
            f"- Venue equity (Polymarket): **${status['venue_equity_usdc']:.2f}** "
            f"(source: {status['venue_equity_source']}, "
            f"age: {status['venue_equity_age_seconds']}s)"
        )
    else:
        lines.append(
            "- Venue equity: _not fetched (run with `--refresh` to query Polymarket)_"
        )
    lines.append(
        f"- Active strategies: {status['active_count']} "
        f"(PAPER + LIVE in lifecycle DB)"
    )
    lines.append(
        f"- Sum of resolved caps: ${status['summed_caps_usdc']:.2f}  "
        f"(headroom: ${status['headroom_usdc']:.2f})"
    )
    if status["pct_sum"] > 0:
        lines.append(f"- Sum of pct allocations: {status['pct_sum'] * 100:.1f}%")
    if status["over_allocated"]:
        lines.append("- :warning: **OVER-ALLOCATED**")
    lines.append("")
    lines.append("| Slug | State | Spec | Resolved cap (USDC) | Source |")
    lines.append("|---|---|---|---:|---|")
    for row in status["per_slug"]:
        lines.append(
            f"| `{row['slug']}` | {row['state']} | {row['cap_spec']} | "
            f"${row['cap_usdc']:.2f} | {row['source']} |"
        )
    if status["warnings"]:
        lines.append("")
        lines.append("## Warnings")
        for w in status["warnings"]:
            lines.append(f"- {w}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Portfolio allocation status")
    fmt = ap.add_mutually_exclusive_group()
    fmt.add_argument("--json", dest="as_json", action="store_true", default=True)
    fmt.add_argument("--md", dest="as_md", action="store_true")
    ap.add_argument(
        "--refresh", action="store_true",
        help="Fetch fresh venue equity from Polymarket before resolving pct caps.",
    )
    ap.add_argument(
        "--no-event", action="store_true",
        help="Don't emit a portfolio_status event into logs/events.jsonl",
    )
    args = ap.parse_args()

    try:
        status = collect_status(refresh=args.refresh)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    if not args.no_event:
        emit_event(
            type="portfolio_status",
            summary=(
                f"{status['active_count']} active; "
                f"caps=${status['summed_caps_usdc']:.2f} / "
                f"envelope=${status['max_total_exposure_usdc']:.2f} "
                f"(equity=${status['venue_equity_usdc']:.2f})"
            ),
            severity="critical" if status["over_allocated"] else "info",
            slug=None,
            data=status,
        )

    if args.as_md:
        print(render_md(status))
    else:
        print(json.dumps(status, indent=2, default=str))

    return 2 if status["over_allocated"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
