# Deployment proposal — operator-agent harness

This is a **proposal** for the remote-machine deployment + agent-harness
architecture. Nothing here is wired yet — the section "What's needed to
actually deploy" at the bottom is the punch-list.

## Goal

Run the agentic loop continuously on a remote machine, with a
**second** agent (the "operator") that watches the system and texts you
when something interesting happens. You shouldn't need to SSH in for
routine updates.

## Process model (proposed)

Two kinds of processes on the remote machine:

1. **The trading lab itself** (everything in this repo). Long-lived
   `paper_run.py` processes per active PAPER strategy + periodic cron
   jobs for the agentic loop drainage.

2. **The operator agent.** A separate Python process (or Claude Code
   `schedule`d agent) that wakes on a cron, reads
   `logs/events.jsonl` + `scripts/operator_briefing.py`, decides what's
   worth telling you, and sends via your preferred channel
   (SMS via Twilio, email, Slack DM, whatever).

The two don't share Python state; they share **the events log**.

```
                                                            +-----------+
   manual_inbox/ + RSS → discover    → propose_hypothesis →
   PROPOSED → codegen runbook        → smoke_test           |
   SMOKE_PASS → eval_strategy        → OPTIMIZE             |
   OPTIMIZE → optimize_strategy      → PAPER_READY          | events.jsonl
   PAPER_READY → [HUMAN]             → PAPER                | append-only
   PAPER → paper_run.py (long-lived) → signals → logs/paper_*.jsonl
   PAPER → paper_summary (:30 hourly)→ reports + experiments row
   PAPER → paper_watcher (:35 hourly)→ HALTED / RETIRED     |
                                                            +-----+
                                                                  |
                                                                  v
                                                           +--------------------+
                                                           | OPERATOR AGENT     |
                                                           | (cron'd, separate) |
                                                           +--------------------+
                                                                  |
                                                              reads events.jsonl
                                                              calls operator_briefing.py
                                                              decides what to send
                                                                  |
                                                                  v
                                                              SMS / Slack / email
```

## Proposed cron schedule (don't apply yet)

Suggested for a Linux box with `cron`; each line is independent and
idempotent.

```cron
TRADING_LAB=/opt/trading-lab
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin

# Hourly: refresh market metadata.
0 * * * *  cd $TRADING_LAB && make sync-markets         >> logs/cron_sync.log 2>&1

# Weekly: full metadata sync (closed + archived too) at 04:00 UTC Sunday.
0 4 * * 0  cd $TRADING_LAB && make sync-markets-full    >> logs/cron_sync.log 2>&1

# Daily 09:00 UTC: drain manual_inbox + (opt) RSS into PROPOSED.
0 9 * * *  cd $TRADING_LAB && make research-discover RSS=1  >> logs/cron_discover.log 2>&1

# Every 6h at minute 7: take the oldest SMOKE_PASS slug and eval it.
7 */6 * * *  cd $TRADING_LAB && SLUG=$(.venv/bin/python scripts/research_cli.py list --state SMOKE_PASS | jq -r '.[-1].slug // empty') ; \
             [ -n "$SLUG" ] && make research-test SLUG="$SLUG" START=$(date -u -d "30 days ago" +\%Y-\%m-\%d) END=$(date -u +\%Y-\%m-\%d) >> logs/cron_test.log 2>&1

# Daily 03:00 UTC: walk-forward optimise the oldest OPTIMIZE slug.
0 3 * * *  cd $TRADING_LAB && SLUG=$(.venv/bin/python scripts/research_cli.py list --state OPTIMIZE | jq -r '.[-1].slug // empty') ; \
           [ -n "$SLUG" ] && make research-optimize SLUG="$SLUG" START=$(date -u -d "30 days ago" +\%Y-\%m-\%d) END=$(date -u +\%Y-\%m-\%d) >> logs/cron_optimize.log 2>&1

# Hourly at :30: summarise every PAPER slug. The watcher needs these rows.
30 * * * *  cd $TRADING_LAB && for slug in $(.venv/bin/python scripts/research_cli.py list --state PAPER | jq -r '.[].slug'); do \
                make paper-summary SLUG="$slug" >> logs/cron_summary.log 2>&1 ; done

# Hourly at :35: auto-retirement watcher, after paper-summary has written
# the latest realised-PnL rows.
35 * * * *  cd $TRADING_LAB && make paper-watcher     >> logs/cron_watcher.log 2>&1

# Every 15 min: operator agent reads events + decides what to forward.
# (This entry assumes an `operator-agent.sh` wrapper that calls
# operator_briefing.py and pipes to your SMS provider. Not provided yet.)
*/15 * * * *  cd $TRADING_LAB && ./operator-agent.sh    >> logs/cron_operator.log 2>&1
```

### Paper-run process supervision

`paper_run.py` is long-lived (runs until killed or `--duration-secs`
elapses). Three options for keeping it up:

1. **systemd user service** (recommended):
   ```ini
   # ~/.config/systemd/user/trading-lab-paper@.service
   [Unit]
   Description=Trading lab paper run for %i
   After=network.target

   [Service]
   WorkingDirectory=/opt/trading-lab
   ExecStart=/opt/trading-lab/.venv/bin/python scripts/paper_run.py --slug %i
   Restart=always
   RestartSec=10
   StandardOutput=append:/opt/trading-lab/logs/paper_%i.stdout.log
   StandardError=append:/opt/trading-lab/logs/paper_%i.stderr.log

   [Install]
   WantedBy=default.target
   ```
   Enable per-slug: `systemctl --user enable trading-lab-paper@tick-mean-revert`.

2. **tmux + a startup script** — fine for an interactive remote box.

3. **Docker container per strategy** — heavier; only if you want
   isolation per strategy.

The operator agent can detect a missing paper run by noticing the gap in
`logs/paper_<slug>_*.jsonl` timestamps (no signals for > 30 min when
state is PAPER → emit a synthetic `paper_run_silent` event).

## Operator-agent contract

The operator agent is **stateless except for the byte-offset cursor it
stores between runs**. Its loop:

```
1. read last cursor from ~/.config/trading-lab-operator/cursor
2. run: python scripts/operator_briefing.py --since-offset <cursor> --json
3. for each item in `forward`: format → send via configured channel
4. write back `new_offset` to cursor file
```

The forward-policy is already baked into `operator_briefing.py`:

  - **All `critical` events** (kill-switch trips, RETIREMENTs) forward.
  - **First `warn` per (type, slug)** per briefing window forwards
    (deduplicated so 50 watcher_halts on the same slug don't spam).
  - **paper_summary deltas > $50** (configurable) forward.
  - Everything else stays in the log for inspection but doesn't send.

### Transport choices

| Channel | Setup | When to use |
|---|---|---|
| Twilio SMS | `pip install twilio` + Twilio account + `TWILIO_*` env | The default for "text me" — direct, no other app needed. |
| Slack DM | Slack bot token + chat.postMessage | If you live in Slack anyway. |
| email | sendmail / SMTP / SES | Lowest-friction; tolerates higher volume. |
| Pushover / ntfy | API key | Free / cheap alternatives to SMS. |

The operator agent is intentionally tiny — pick one transport and write
~30 lines around `operator_briefing.py`'s output. An example skeleton:

```python
# operator-agent.py (NOT committed — lives on the remote machine)
import json
import os
import subprocess
from pathlib import Path
from twilio.rest import Client

CURSOR = Path.home() / ".config" / "trading-lab-operator" / "cursor"
CURSOR.parent.mkdir(parents=True, exist_ok=True)
last_offset = int(CURSOR.read_text().strip()) if CURSOR.exists() else 0

proc = subprocess.run(
    ["/opt/trading-lab/.venv/bin/python", "scripts/operator_briefing.py",
     "--since-offset", str(last_offset), "--json"],
    cwd="/opt/trading-lab", capture_output=True, text=True, check=True,
)
payload = json.loads(proc.stdout.strip().splitlines()[-1])

tw = Client(os.environ["TWILIO_SID"], os.environ["TWILIO_TOKEN"])
for ev in payload["forward"]:
    body = f"[{ev['severity'].upper()}] {ev['summary']}"
    tw.messages.create(
        to=os.environ["MY_NUMBER"],
        from_=os.environ["TWILIO_FROM"],
        body=body[:160],
    )

CURSOR.write_text(str(payload["new_offset"]))
```

The "should this message me?" decision is entirely in
`operator_briefing.py`'s forward-policy. To tune what's noisy, change that
script, not the operator agent.

## Security envelope

- The operator agent runs on the same machine — no network surface added.
- It only reads `logs/events.jsonl` (append-only, read-safe).
- Outbound: only the agent's chosen transport. SMS/Slack/email creds
  live in the operator agent's `.env`, not in this repo's `.env`.
- The trading lab's `.env` (with `POLY_PRIVATE_KEY` etc) is NEVER read
  by the operator agent.
- Kill switch: `scripts/halt_trading.py --reason "..."` from anywhere
  on the machine will halt all paper runs on next watcher tick — and
  emit a `critical` event the operator will text you about.

## What's needed to actually deploy

These are pre-deployment to-dos, not code-in-the-repo to-dos:

1. **Provision the remote machine.** Linux box with Python 3.12 + uv.
   Clone the repo, `make dev`.
2. **Copy your `.env`** with credentials. NEVER check this in.
3. **Pick a supervision method** (systemd preferred) for `paper_run.py`.
4. **Pick a transport** (Twilio / Slack / email).
5. **Write the operator agent wrapper** (~30 lines, sketch above) with
   transport creds in its own `.env`.
6. **Install the cron entries.** Start with the watcher + summariser; add
   discovery/eval/optimize once you have data flowing.
7. **One-time data backfill:** `make sync-markets-full` + download trade
   history for every market your active hypotheses will touch.

## What this repo provides today

✅ Events log architecture (`logs/events.jsonl` + `emit_event`)
✅ `operator_briefing.py` with built-in forward-policy
✅ All scripts emit events on state transitions, watcher decisions,
   summariser runs, kill-switch trips
✅ All scripts are JSON-out / exit-code aware — cron-compatible

🚫 No transport adapter shipped (Twilio / Slack / email) — that's
   intentionally yours to pick.
🚫 No systemd unit files shipped — paste the sketch above.
🚫 No actual cron entries — `docs/scheduling.md` has the recipes, but
   wiring is a deployment-machine concern.
