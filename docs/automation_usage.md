# JanQ automation usage

The automation runner has two modes:

- `dry_run`: observes probe events and writes decisions, but never clicks.
- `ui_live`: sends conservative UI input to the copied client window.

The default is always `dry_run`.

## Start with dry-run

```powershell
$env:PYTHONPATH = "src"
python -m janq_lab.automation.bot --config automation.example.yaml --mode dry_run
```

Session logs are written under `_runtime/sessions` unless
`--session-log-path` is provided.

## UI live mode

Use this only after `dry_run` decisions look correct in the session log.

```powershell
$env:PYTHONPATH = "src"
python -m janq_lab.automation.bot --config automation.example.yaml --mode ui_live --max-hands 20
```

The runner waits for probe confirmation after each live UI action. If the
expected `send_action_*` event does not appear before the confirmation timeout,
the runner writes `bot_pause` and stops.

## Bot log events

- `bot_state`: current reduced state from probe events.
- `bot_decision`: selected action and policy details.
- `bot_action_done`: dry-run result or UI click details.
- `bot_confirmed`: live action observed through `JanqProbe`.
- `bot_pause`: stop or safety pause reason.
- `bot_session_summary`: final state and pending action, if any.

## Probe requirements

Build the probe after changes:

```powershell
dotnet build plugin\JanqProbe\JanqProbe.csproj -c Release
```

The probe now emits `game_state_snapshot` events in addition to JanQ API
send/receive events. The snapshots are read-only and are used to avoid acting
when the UI state is uncertain.
