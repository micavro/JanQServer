# JanQ automation usage

The automation runner has three modes:

- `dry_run`: observes probe events and writes decisions, but never clicks.
- `plugin_live`: queues actions for the in-client BepInEx bridge. This is the
  preferred live mode because it does not move the system mouse or require the
  game window to be focused.
- `ui_live`: legacy foreground mouse input. Keep this as a fallback only.

The default is always `dry_run`.

## Cold start

The copied client must already have a saved account that can use the normal
login button. Start the copied game and bot together with:

```powershell
.\start_janq_bot.ps1 -MaxHands 20
```

The script starts only the copied `sega_net_MJ\MJ\MJ.exe`, minimizes it, and
then starts `plugin_live`. The bridge advances the known startup screens,
uses the client's saved-account login action, enters Casino -> JanQ, and
selects the minimum currently available normal bet. Credentials are neither
read nor written by the bridge.

The game sets `Application.runInBackground`, so it continues updating while
minimized or unfocused. Actions use in-client handlers and do not move the
system cursor.

## Start with dry-run

```powershell
$env:PYTHONPATH = "src"
python -m janq_lab.automation.bot --config automation.example.yaml --mode dry_run
```

Session logs are written under `_runtime/sessions` unless
`--session-log-path` is provided.

## Plugin live mode

Use this only after `dry_run` decisions look correct in the session log.

```powershell
$env:PYTHONPATH = "src"
python -m janq_lab.automation.bot --config automation.example.yaml --mode plugin_live --max-hands 20
```

The runner writes a short-lived command under `_runtime/bridge/commands`. The
plugin validates the current JanQ state and invokes the same local
`GameManager` button methods used by the UI. Shot commands set the local gauge
to a safe point inside the selected area before invoking `MainButtonClick`.
With `enter_janq_on_start: true`, the runner first arms automatic startup and
Casino navigation.

The runner waits for probe confirmation after each live action. If the
expected `send_action_*` event does not appear before the confirmation timeout,
the runner writes `bot_pause` and stops.

`max_hands` counts hands completed during the current bot session. A win result
or automatic exhaustive draw ends a hand. The runner stops immediately at the
limit before another bet can be placed.

`ui_live` remains available for comparison:

```powershell
python -m janq_lab.automation.bot --config automation.example.yaml --mode ui_live --max-hands 20
```

## Bot log events

- `bot_state`: current reduced state from probe events.
- `bot_bootstrap_state`: current state reconstructed before live actions begin.
- `bot_startup_action`: result of the optional `enter_janq` command.
- `bot_decision`: selected action and policy details.
- `bot_action_done`: dry-run, bridge, or UI action result.
- `bot_confirmed`: live action observed through `JanqProbe`.
- `bot_pause`: stop or safety pause reason.
- `bot_session_summary`: final state and pending action, if any.

## Probe requirements

Build the probe after changes:

```powershell
dotnet build plugin\JanqProbe\JanqProbe.csproj -c Release
```

The probe emits `game_state_snapshot` events in addition to JanQ API
send/receive events. API events remain authoritative across transient animation
snapshots, preventing duplicate shots and premature final-ball discards.
