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

The script starts only the copied `sega_net_MJ\MJ\MJ.exe` in windowed mode
(`-screen-fullscreen 0`, default 1280x720), optionally minimizes it, and then
starts `plugin_live`. The bridge advances the known startup screens,
uses the client's saved-account login action, enters Casino -> JanQ, and
selects the configured normal bet. Credentials are neither read nor written by
the bridge.

The game sets `Application.runInBackground`, so it continues updating while
minimized or unfocused. Actions use in-client handlers and do not move the
system cursor.

If login hits a known error dialog, such as the same account being active on
another device, the probe records `janq_navigation_login_dialog_dismissed` and
returns to the title flow without moving the mouse. Login-error dialogs are
tagged with `dialogReason: account_conflict_or_login_error`. If the same
dialog repeats three times, the probe records `janq_navigation_login_blocked`
and the runner pauses with `login_blocked_or_repeated_dialog`.

If the same login-error dialog appears while the bot is already in a live run,
the probe records `janq_runtime_login_dialog_observed` and
`janq_runtime_login_blocked`. The runner pauses with the same
`login_blocked_or_repeated_dialog` reason instead of sending more game actions.

## Bet tier policy

The live runner is the only bet-policy owner. It writes
`_runtime/bridge/settings.json` before navigation and whenever observed
`mjchip` changes the target tier. The plugin reads that file when the Casino
JanQ bet menu is built and selects the requested `targetBet` if it is
available.

With `auto_reselect_bet: true`, the runner will not press BET when the actual
selected tier is unknown, stale, or different from the current policy target.
Instead it sends `reselect_bet`; the plugin jumps back through Casino -> JanQ
from a safe `BetWait` point and rebuilds the bet menu so the new target can be
selected. The runner resumes only after `janq_navigation_bet_selected`
confirms the current target. If the plugin already tried the current target
and could only select a fallback candidate, the runner pauses instead of
looping or betting the wrong tier.

Default bankroll policy is the 200/100 ladder:

- ladder: `10,20,30,50,100,200`
- upgrade: bankroll `>= 200 * next_bet`
- downgrade: bankroll `< 100 * current_bet`
- absolute run target: `target_mjchip: 1000000`

For a 10G -> 20G selection test, force the target tier at startup:

```powershell
.\start_janq_bot.ps1 -MaxHands 2 -ForcedBet 20
```

The probe records `janq_navigation_bet_selected` with the target bet, selected
bet, selection mode, level, and eligible candidates.

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
For shot actions, the runner also checks the confirmed area payload; if the
plugin requested one area but the client sends another, it pauses with
`confirmation_payload_mismatch:send_action_shot`.

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
- `bot_bet_policy`: target bet tier chosen from current bankroll.
- `bot_bet_selected`: bet tier confirmed by the Casino JanQ menu.
- `bot_bet_reselect_requested`: runner requested a Casino -> JanQ reselect
  before pressing BET.
- `login_blocked_or_repeated_dialog`: login repeatedly returned an error
  dialog, or a login-error dialog appeared during a live run, commonly because
  the account is still active elsewhere.
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
