# Continuous operation — scheduling the agentic loop

The agentic loop is invocation-driven by design; running it continuously is
just a matter of scheduling. Two options:

## Option A — cron (recommended for local dev)

Add these entries via `crontab -e`. Paths assume the repo is at
`/home/$USER/Code/Trading-Lab` — adjust as needed.

```cron
# Trading-Lab agentic loop — runs all in repo root via `make` so .venv is honoured.
TRADING_LAB=/home/$USER/Code/Trading-Lab

# Daily: refresh market metadata at 04:00 UTC, full sync once a week.
0 4 * * *  cd $TRADING_LAB && make sync-markets         >> logs/cron_sync.log  2>&1
0 4 * * 0  cd $TRADING_LAB && make sync-markets-full    >> logs/cron_sync.log  2>&1

# Every 6h: poll public strategy sources into manual_inbox.
0 */6 * * *  cd $TRADING_LAB && make research-capture SOURCE_ARGS='--all' >> logs/cron_capture.log 2>&1

# Daily: drain manual_inbox into PROPOSED (RSS opt-in via RSS=1).
0 9 * * *  cd $TRADING_LAB && make research-discover    >> logs/cron_discover.log 2>&1

# Every 6h: take one SMOKE_PASS or BACKTEST slug and run eval. The script
# is single-shot; an outer loop / agent can drive selecting which slug.
0 */6 * * *  cd $TRADING_LAB && SLUG=$(.venv/bin/python scripts/research_cli.py list --state SMOKE_PASS | jq -r '.[-1].slug // empty') ; \
             [ -n "$SLUG" ] && make research-test SLUG="$SLUG" START=2026-05-24 END=$(date -u +\%Y-\%m-\%d) >> logs/cron_test.log 2>&1

# Daily 03:00: walk-forward optimise the oldest OPTIMIZE slug.
0 3 * * *  cd $TRADING_LAB && SLUG=$(.venv/bin/python scripts/research_cli.py list --state OPTIMIZE | jq -r '.[-1].slug // empty') ; \
           [ -n "$SLUG" ] && make research-optimize SLUG="$SLUG" START=2026-05-24 END=$(date -u +\%Y-\%m-\%d) >> logs/cron_optimize.log 2>&1

# Hourly at :30: roll up today's paper-trades jsonl into a summary report.
# Schedule per-slug; here's the pattern (one line per slug).
30 * * * *  cd $TRADING_LAB && make paper-summary SLUG=tick-mean-revert >> logs/cron_summary.log 2>&1

# Hourly at :35: auto-retirement watcher (single-day + 7-day drawdown rules).
# Runs after paper-summary so it sees the latest realised-PnL rows.
35 * * * *  cd $TRADING_LAB && make paper-watcher     >> logs/cron_watcher.log  2>&1
```

## Option B — `loop` skill (interactive sessions)

Inside a Claude Code session you can pin a recurring task with the `loop`
skill. Example:

```
/loop 15m make paper-watcher
```

This re-fires `make paper-watcher` every 15 minutes; useful when you have
a session open and want the watcher running without an OS cron entry.

## Option C — `schedule` skill (remote / always-on)

If you want the loop running when your laptop is asleep, the `schedule`
skill creates a remote agent that fires on cron. Same commands as above
but executed by an Anthropic-hosted runner. See the skill docs.

## Budget guardrails (still in force regardless of scheduler)

`eval_strategy.py` and `optimize_strategy.py` both call
`agent.budget.check("backtests")` before doing any work and exit early
with `{"ok": false, "error": "budget_exhausted"}` once the daily cap
(default 50) is hit. The cron entries above are designed to be
re-triggered safely.

## Safety: the watcher is your seatbelt

`make paper-watcher` is the only piece that auto-transitions PAPER
strategies (to HALTED on single-day -5%, to RETIRED on 7d -15%). Don't
skip it — without it the loop has no protective backstop once a
strategy is in PAPER.

The global kill switch (`data/.kill_switch`) is a separate, stronger
gate: trip it via `scripts/halt_trading.py --reason ...` and every
PAPER strategy is immediately HALTED on the watcher's next run.
