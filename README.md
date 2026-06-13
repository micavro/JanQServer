# JanQ Lab

Fresh rebuild for JanQ research and EV validation.

This repository keeps the old automation scripts only as reference material.
New code lives under `src/janq_lab`.

## Current focus

1. Parse the official JanQ table assets from the copied client.
2. Build an offline simulator.
3. Add a passive client probe.
4. Compare policies with real minimum-bet captures.

## Useful commands

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
python -m janq_lab.assets.nyukyu
python -m janq_lab.assets.special
python -m janq_lab.analysis.monte_carlo --hands 1000 --seed 20260612 --strategy public
python -m janq_lab.analysis.economy_monte_carlo --sessions 50000 --seed 20260612 --bet 10 --strategy public
python -m janq_lab.analysis.economy_monte_carlo --sessions 10000 --seed 20260613 --bet 10 --strategy route_ev
python -m janq_lab.probe.replay _runtime\logs\janq_events.jsonl
python -m janq_lab.analysis.haipai_distribution _runtime\logs\janq_events.jsonl --baseline-hands 10000
python -m janq_lab.analysis.shot_distribution _runtime\logs\janq_events.jsonl
python -m janq_lab.analysis.observed_ev _runtime\logs\janq_events.jsonl --currency mjchip
python -m janq_lab.automation.bot --config automation.example.yaml --mode dry_run
```

Runtime logs and generated artifacts belong under `_runtime`.

## Passive probe

The copied client has BepInEx installed under `sega_net_MJ\MJ`.
Build the passive logger with:

```powershell
dotnet build plugin\JanqProbe\JanqProbe.csproj -c Release
```

See `docs/probe_usage.md` for event names and log paths.
See `docs/automation_usage.md` for dry-run and UI-live automation.

## Interpretation

The Monte Carlo command currently reports simplified win rate for the fast
public-heuristic baseline. The economy Monte Carlo applies the official payout
table and the copied client's special-mode tables. Real-money-like EV validation
still needs captured `recv_config_odds`, `recv_game_haipai`, `recv_game_tsumo`,
and `recv_janq_result` events from minimum-bet real play.

Normal-game initial hands are treated as a replaceable source. The default is a
physical-wall baseline, but economy simulations can bootstrap from captured
`recv_game_haipai` samples with:

```powershell
python -m janq_lab.analysis.economy_monte_carlo --sessions 10000 --strategy route_ev --normal-haipai-source observed --observed-events _runtime\logs\janq_events.jsonl
```

Probe payloads are normalized before model comparison because `JanQAPI` uses
1-based tile ids while `janq_lab` uses the copied client's 0-based table ids.

See `docs/janq_rules_economy.md` for the current game-flow and reward-table
assumptions.
See `docs/route_ev_strategy.md` for the current route-aware strategy notes.
