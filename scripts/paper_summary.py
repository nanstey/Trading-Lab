#!/usr/bin/env python3
"""
Summarise a paper-trading run: pair entry/close signals, compute realised
PnL, write a markdown report.

Each line in `logs/paper_<slug>_<date>.jsonl` is one signal emitted by
`GenericPaperRunner`. Strategies that emit symmetric BUY/SELL pairs (e.g.
tick-mean-revert: entry then opposite-side close) yield realised PnL =
(close_price - entry_price) * size for longs, inverted for shorts.

Pairing heuristic: signals are matched FIFO per (token_id, side-transition).
For a BUY then SELL at the same token, the pair realises:
    pnl = (sell.price - buy.price) * min(sell.qty, buy.qty)
Unmatched signals at end-of-window are reported separately.

Output:
  - `research/paper_reports/<slug>_<date>.md` — human-readable summary
  - One `experiments` row with kind="paper_summary" recording the realised
    PnL so the auto-retirement watcher can read it cheaply.

Usage:
  python scripts/paper_summary.py --slug tick-mean-revert
  python scripts/paper_summary.py --slug tick-mean-revert --date 20260525
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

log = logging.getLogger("paper_summary")

LOG_DIR = Path("logs")
REPORT_DIR = Path("research/paper_reports")


@dataclass
class FillPair:
    token_id: str
    entry_ts: str
    entry_side: str
    entry_price: float
    close_ts: str
    close_side: str
    close_price: float
    matched_qty: float

    @property
    def pnl_usdc(self) -> float:
        # entry_side is the OPENING side. BUY entry → long → close=SELL.
        # We treat ANY entry-then-opposite pair as a (long pnl) interpretation.
        if str(self.entry_side) == "1":  # OrderSide.BUY
            return (self.close_price - self.entry_price) * self.matched_qty
        else:
            return (self.entry_price - self.close_price) * self.matched_qty


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True)
    p.add_argument(
        "--date",
        default=datetime.now(tz=UTC).strftime("%Y%m%d"),
        help="YYYYMMDD of the log to summarise (default: today UTC)",
    )
    p.add_argument(
        "--db", type=Path, default=Path("research/experiments.db"),
    )
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.WARN if args.quiet else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    log_path = LOG_DIR / f"paper_{args.slug}_{args.date}.jsonl"
    if not log_path.exists():
        print(json.dumps({
            "ok": False, "error": "log_not_found",
            "path": str(log_path),
        }))
        return 2

    signals = _read_signals(log_path)
    if not signals:
        print(json.dumps({
            "ok": False, "error": "empty_log", "path": str(log_path),
        }))
        return 2

    pairs, unmatched = _pair_signals(signals)
    total_pnl = sum(p.pnl_usdc for p in pairs)
    per_token = defaultdict(lambda: {"pairs": 0, "pnl": 0.0})
    for fp in pairs:
        per_token[fp.token_id]["pairs"] += 1
        per_token[fp.token_id]["pnl"] += fp.pnl_usdc

    first_ts = signals[0]["ts_iso"]
    last_ts = signals[-1]["ts_iso"]

    report_md = _render_report(
        slug=args.slug,
        date=args.date,
        first_ts=first_ts,
        last_ts=last_ts,
        n_signals=len(signals),
        pairs=pairs,
        unmatched=unmatched,
        per_token=per_token,
        total_pnl=total_pnl,
    )
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"{args.slug}_{args.date}.md"
    out_path.write_text(report_md)

    # Record into experiments DB so the auto-retirement watcher (and other
    # readers) can query realised paper PnL without re-parsing the jsonl.
    _record_into_db(
        slug=args.slug,
        date=args.date,
        pairs=pairs,
        total_pnl=total_pnl,
        db_path=args.db,
    )

    # Emit a paper_summary event so the operator-briefing agent sees
    # progress at a glance.
    try:
        from trading_lab.agent.events import emit_event

        emit_event(
            type="paper_summary",
            summary=(
                f"{args.slug} {args.date}: {len(pairs)} pairs, "
                f"realised PnL ${total_pnl:.2f}"
            ),
            severity="info",
            slug=args.slug,
            data={
                "date": args.date,
                "n_signals": len(signals),
                "n_pairs": len(pairs),
                "n_unmatched": len(unmatched),
                "realised_pnl_usdc": round(total_pnl, 4),
                "report_path": str(out_path),
            },
        )
    except Exception:
        pass

    print(json.dumps({
        "ok": True,
        "slug": args.slug,
        "date": args.date,
        "report_path": str(out_path),
        "n_signals": len(signals),
        "n_pairs": len(pairs),
        "n_unmatched": len(unmatched),
        "realised_pnl_usdc": round(total_pnl, 4),
        "window": {"first_ts": first_ts, "last_ts": last_ts},
    }))
    return 0


def _read_signals(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _pair_signals(signals: list[dict]) -> tuple[list[FillPair], list[dict]]:
    """
    FIFO match per token: each open entry is closed by the next opposite-side
    signal on the same token.
    """
    open_q: dict[str, deque[dict]] = defaultdict(deque)
    pairs: list[FillPair] = []
    for s in signals:
        token = s["token_id"]
        side = str(s["side"])
        q = open_q[token]
        # Match against the FIRST opposite-side open signal.
        matched_idx = None
        for i, opener in enumerate(q):
            if str(opener["side"]) != side:
                matched_idx = i
                break
        if matched_idx is not None:
            opener = q[matched_idx]
            qty = min(float(opener["quantity"]), float(s["quantity"]))
            pairs.append(FillPair(
                token_id=token,
                entry_ts=opener["ts_iso"],
                entry_side=opener["side"],
                entry_price=float(opener["price"]),
                close_ts=s["ts_iso"],
                close_side=side,
                close_price=float(s["price"]),
                matched_qty=qty,
            ))
            del q[matched_idx]
        else:
            q.append(s)

    unmatched = [s for token_q in open_q.values() for s in token_q]
    return pairs, unmatched


def _render_report(
    *,
    slug: str,
    date: str,
    first_ts: str,
    last_ts: str,
    n_signals: int,
    pairs: list[FillPair],
    unmatched: list[dict],
    per_token: dict,
    total_pnl: float,
) -> str:
    wins = sum(1 for p in pairs if p.pnl_usdc > 0)
    losses = sum(1 for p in pairs if p.pnl_usdc < 0)
    flats = len(pairs) - wins - losses
    win_rate = (wins / len(pairs) * 100) if pairs else 0.0
    avg_pnl = (total_pnl / len(pairs)) if pairs else 0.0
    lines = [
        f"# Paper trading report: {slug} ({date})",
        "",
        f"- **Window**: `{first_ts}` → `{last_ts}`",
        f"- **Signals**: {n_signals}",
        f"- **Matched pairs**: {len(pairs)}",
        f"- **Unmatched signals**: {len(unmatched)}",
        f"- **Realised PnL**: **${total_pnl:.4f}**",
        f"- **Win/Loss/Flat**: {wins} / {losses} / {flats}  ({win_rate:.1f}% win rate)",
        f"- **Mean PnL per pair**: ${avg_pnl:.4f}",
        "",
        "## Per-token breakdown",
        "",
        "| Token (first 14) | Pairs | PnL (USDC) |",
        "|---|---|---|",
    ]
    for token, stats in sorted(per_token.items(), key=lambda kv: -kv[1]["pnl"]):
        lines.append(
            f"| `{token[:14]}..` | {stats['pairs']} | ${stats['pnl']:.4f} |"
        )
    if unmatched:
        lines += [
            "",
            "## Unmatched (open-position-at-end) signals",
            "",
            "| Side | Price | Qty | Token (first 14) | Timestamp |",
            "|---|---|---|---|---|",
        ]
        for s in unmatched[:20]:
            lines.append(
                f"| {s['side']} | {s['price']} | {s['quantity']} | "
                f"`{s['token_id'][:14]}..` | {s['ts_iso']} |"
            )
        if len(unmatched) > 20:
            lines.append(f"| _...and {len(unmatched) - 20} more_ |")
    lines.append("")
    return "\n".join(lines)


def _record_into_db(*, slug, date, pairs, total_pnl, db_path: Path) -> None:
    from trading_lab.agent import lifecycle

    if not pairs:
        return
    first = pairs[0].entry_ts
    last = pairs[-1].close_ts
    try:
        lifecycle.record_experiment(
            slug=slug,
            params={"_paper_summary": True, "date": date},
            data_start=first,
            data_end=last,
            sharpe=0.0,                 # not meaningful for live paper
            max_dd=0.0,                 # could compute; skipped for v1
            fill_rate=1.0,
            pnl=float(total_pnl),
            n_trades=len(pairs) * 2,    # each pair = 2 fills
            db_path=db_path,
        )
    except Exception as exc:
        log.warning("failed to record paper summary: %s", exc)


if __name__ == "__main__":
    sys.exit(main())
