# JanqProbe usage

`JanqProbe` is a passive BepInEx plugin for the copied MJ client. It records
JanQ API actions and server responses as JSONL.

## Installed client

BepInEx 5.4.23.5 x64 was installed into:

```text
C:\Users\micavro\Desktop\JanQ\sega_net_MJ\MJ
```

The original installation under `C:\Program Files (x86)\SEGA` is not modified.

The plugin DLL is copied here on build:

```text
C:\Users\micavro\Desktop\JanQ\sega_net_MJ\MJ\BepInEx\plugins\JanqProbe.dll
```

## Build

```powershell
dotnet build plugin\JanqProbe\JanqProbe.csproj -c Release
```

## Logs

Default JSONL output:

```text
C:\Users\micavro\Desktop\JanQ\_runtime\logs\janq_events.jsonl
```

Override path if needed:

```powershell
$env:JANQ_PROBE_LOG = "C:\Users\micavro\Desktop\JanQ\_runtime\logs\custom_janq_events.jsonl"
```

## Event types

Passive/server-side events:

- `recv_config_odds`
- `recv_game_haipai`
- `recv_game_tsumo`
- `recv_act_dahai`
- `recv_janq_result`
- `game_state_snapshot`

Action-observation events:

- `send_action_start`
- `send_action_shot`
- `send_action_dahai`
- `send_action_agari`
- `send_ryukyoku`
- `send_give_up`

Lifecycle events:

- `probe_loaded`
- `probe_unloaded`

Important: these payloads are recorded from `JanQAPI` and may contain raw
server/API tile ids. `Api.ApiClient` converts many tile ids to 0-based ids
before `GameManager` uses them. The Python model uses 0-based ids internally,
so replay analysis must normalize before comparing against model output.

## Smoke test already performed

The copied `MJ.exe` was started once after installation. BepInEx loaded
successfully and wrote:

```text
JanQ Probe loaded; logging to C:\Users\micavro\Desktop\JanQ\_runtime\logs\janq_events.jsonl
```

The JSONL contains `probe_loaded` and `probe_unloaded`, so the loader path and
plugin copy are confirmed. No gameplay sample has been captured yet.

## Read events in Python

```powershell
$env:PYTHONPATH = "src"
@'
from janq_lab.probe.events import count_by_type, read_events
events = list(read_events(r"_runtime\logs\janq_events.jsonl"))
print(count_by_type(events))
'@ | python -
```

## Replay and EV summaries

```powershell
$env:PYTHONPATH = "src"
python -m janq_lab.probe.replay _runtime\logs\janq_events.jsonl
python -m janq_lab.analysis.shot_distribution _runtime\logs\janq_events.jsonl
python -m janq_lab.analysis.observed_ev _runtime\logs\janq_events.jsonl --currency mjchip
```

The current smoke-test log only contains lifecycle events, so the EV summary
will report `hands: 0` until at least one real JanQ hand is captured.
The shot-distribution report will likewise report `total_shots: 0` until real
`send_action_shot`/`recv_game_tsumo` pairs are present.
