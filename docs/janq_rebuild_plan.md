# JanQ rebuild plan

Date: 2026-06-12

## Current state

- The original SEGA NET Mahjong MJ installation was copied from
  `C:\Program Files (x86)\SEGA\sega_net_MJ` to
  `C:\Users\micavro\Desktop\JanQ\sega_net_MJ`.
- The copied executable `sega_net_MJ\MJ\MJ.exe` was able to launch from the
  workspace copy. Future hooks, plugins, screenshots, and logs should target
  this copy, not the original install directory.
- Runtime artifacts are reserved under `_runtime\`:
  - `_runtime\logs`
  - `_runtime\screenshots`
  - `_runtime\dumps`
  - `_runtime\experiments`
  - `_runtime\captures`
- `ilspycmd` 9.1.0 was installed and used to decompile JanQ-related client
  code into `_runtime\dumps\decompiled_janq`.
- Desktop computer-use is currently blocked by a local CUA Node runtime export
  error. This is logged in `_runtime\logs\tooling.log`. Static analysis and
  copied-client experiments can continue without it.

## What JanQ is

SEGA's official help describes JanQ as a casino game where the player fires
balls toward wanted tile pockets and makes mahjong hands. It has normal play
and special modes such as challenge/bonus/free-play behavior.

Public strategy posts mostly agree on the rough heuristic:

- Manzu are easiest on the left side.
- Dragon tiles are around the center.
- Pinzu are on the right side.
- Souzu are harder because they are split/obstructed.
- Dyeing hands and Daisangen/yakuman routes are the public "big win" line.

But at least one public trial article warns that old攻略 can be stale and
recommends validating with minimum bet first. Treat all public claims as
hypotheses, not truth.

## Local client facts

The client is much more informative than the old project assumed.

### Shot model

`Janq.GameManager.MainButtonClick()` converts the shot gauge fill amount into
an integer `area` through `MakeShotArea(float)`:

```text
area thresholds = [0.20, 0.305, 0.39, 0.48, 0.52, 0.61, 0.69]
return last threshold below fillAmount + 1
```

The final request is `JanQAPI.sendActionShot(int area)`, which serializes only
that 1-7 area number. The drawn tile is returned later by the server through
`RecvGameTsumo`.

This means the strategy engine should choose an area number, and the execution
layer can either:

- emulate the UI gauge to hit that area, or
- in a controlled local probe, observe/hook the API call.

It should not continue relying on brittle hard-coded mouse timings as the core
model.

### Server-state objects

The useful authoritative receive points are:

- `JanQAPI.RecvGameHaipai`
  - initial hand, dora/ura-dora, `zandan`, mode/status, tenhou flag.
- `JanQAPI.RecvGameTsumo`
  - shot result tile, remaining balls, replay flag, reach flag, updated hand.
- `JanQAPI.RecvActDahai`
  - accepted discard/reach state.
- `JanQAPI.RecvJanQResult`
  - win flag, han/yakuman/yaku level, odds, renchan, final hand, yaku list,
    yakuman count.
- `JanQAPI.RecvConfigOdds`
  - live yaku odds table from the server.

The old project watched UI-ish logs such as `SetNyukyuBlack`. The new project
should log these as helpful debug signals only, while treating `JanQAPI`
response objects as ground truth.

### API conversion details

`Api.ApiClient` confirms that the server-facing `JanQAPI` objects use 1-based
tile ids in several places, while `GameManager` and the local tables use
0-based ids:

- `SetHaipai` subtracts 1 from `spGameHaipai.haipai`, `omoDora`, and `uraDora`.
- `Tumo` subtracts 1 from `spGameTsumo.tehai`, `pai`, `omo_dora`, and
  `ura_dora`.
- `Dahai` sends `pai_id + 1`.
- `Agari` sends each local tile as `item + 1`.

The Python model uses 0-based ids internally. The probe records raw API
payloads, so replay and model-comparison code must normalize carefully.

### Probability table

The table files under
`sega_net_MJ\MJ\MJ_Data\StreamingAssets\Janq\table` include exact local weight
tables:

- `nyukyu_base_table.bytes`
- `nyukyu_paren_table.bytes`
- `nyukyu_yakuman_table.bytes`

Each is 476 bytes, and parsing as 34 tiles x 7 areas x uint16 little-endian
gives columns summing to 10000. The three nyukyu tables are currently identical
in the copied client.

Tile order used by the client:

```text
0-8   = 1m..9m
9-17  = 1s..9s
18-26 = 1p..9p
27-33 = E,S,W,N,P,F,C
```

Area distribution, normalized:

```text
area 1: 1m 12%, 2m 12%, 3m 12%, 4m 12%, 5m 12%, 6m 8%, 7m 5%, 8m 4%, 9m 3%, E 20%
area 2: 3m 1%, 4m 2%, 5m 6%, 6m 12%, 7m 12%, 8m 12%, 9m 12%, 1s 7%, 2s 6%, 3s 5%, 4s 4%, 5s 3%, 6s 2%, 7s 1%, E 10%, S 5%
area 3: 3m 1%, 4m 1%, 5m 2%, 6m 3%, 7m 4%, 8m 5%, 9m 2%, 1s 11%, 2s 12%, 3s 12%, 4s 12%, 5s 11%, 6s 3%, 7s 1%, S 20%
area 4: mixed center; dragons P/F/C 20% each, S 4%, W 4%, plus small suit weights
area 5: 3s 1%, 4s 3%, 5s 11%, 6s 12%, 7s 12%, 8s 12%, 9s 11%, 1p 2%, 2p 5%, 3p 4%, 4p 3%, 5p 2%, 6p 1%, 7p 1%, W 20%
area 6: 3s 1%, 4s 2%, 5s 3%, 6s 4%, 7s 5%, 8s 6%, 9s 7%, 1p 12%, 2p 12%, 3p 12%, 4p 12%, 5p 6%, 6p 2%, 7p 1%, W 5%, N 10%
area 7: 1p 3%, 2p 4%, 3p 5%, 4p 8%, 5p 12%, 6p 12%, 7p 12%, 8p 12%, 9p 12%, N 20%
```

This table should replace the old hard-coded `config.py` probability table.

### Special-mode setup tables

Additional resource tables under `StreamingAssets\Janq\table` are now parsed by
`janq_lab.assets.special`:

- `paren_select_table.bytes`
  - 11 two-byte pairs: `(2,1)` through `(12,1)`.
- `paren_2_table.bytes` through `paren_12_table.bytes`
  - each is 80 bytes.
  - each parses as 5 records x 16 bytes:
    `enabled + 13 tile ids + dora id + ura-dora id`.
- `yakuman_select_table.bytes`
  - one pair: `(99,1)`.
- `yakuman_table.bytes`
  - 70 bytes = 5 records x 14 bytes:
    `enabled + 13 tile ids`.
- `yakuman_tenho_table.bytes`
  - 75 bytes = 5 records x 15 bytes:
    `enabled + 14 tile ids`.
- `doukei_select_table.bytes` and `doukei_table.bytes`
  - preserved as raw values for now; their exact semantics still need deeper
    confirmation.

These tables appear to provide special-mode starting hand candidates, not
shot-probability transitions. The next EV-modeling step is to connect real
`RecvGameHaipai` logs from challenge/yakuman modes to these tables and confirm
the selection mechanics empirically.

### Four-copy behavior

The client checks whether the drawn tile makes a count of four, plays a
plus-ball animation, then sets `mBalls = spGameTsumo.zandan`. The simulator
should model the fourth-copy extra ball, but the passive logger should verify
the exact server behavior by comparing pre-shot and post-shot `zandan`.

## Problems in the old project

Keep the old files only as requirements archaeology.

- `config.py` hard-codes the original install path:
  `C:\Program Files (x86)\SEGA\sega_net_MJ\MJ\BepInEx\LogOutput.log`.
- The copied/original game currently has `doorstop_config.ini`, but no complete
  `BepInEx` folder was found. The old log path is not reliable.
- Importing `game_control.py` creates a global `GameControl()` immediately,
  making imports have side effects and requiring the game window too early.
- `press_areas.py` and `game_control.py` depend on fixed window coordinates and
  an `actuator.exe` timing helper.
- `JanQcore\decision.py` calls `mahjong-helper.exe` as an external process.
- `decision.py` mutates imported probability data (`probs_now = probs`), so
  repeated decisions are not independent.
- Strategy, IO, window control, statistics, and game-state parsing are all
  coupled together.
- Several files display mojibake, making maintenance risky.

## Recommended architecture

Build a new project from zero beside the copied client. Suggested package name:
`janq_lab`.

```text
janq_lab/
  pyproject.toml
  README.md
  src/janq_lab/
    assets/
      nyukyu.py          # parse .bytes tables into typed probability matrices
      tables.py          # tile naming and table discovery
    model/
      state.py           # GameState, Hand, Mode, Result dataclasses
      transitions.py     # shot/discard/result transitions
      scoring.py         # yaku/han/odds abstraction
      simulator.py       # Monte Carlo and exact rollouts
    strategy/
      baseline.py        # public heuristic: dye/Daisangen/etc.
      greedy_ev.py       # immediate EV and shanten-improvement policies
      search.py          # DP/MCTS policy search
    probe/
      jsonl.py           # event schema and readers
      replay.py          # rebuild games from logs
    analysis/
      ev_report.py       # confidence intervals and bankroll charts
  plugin/
    JanqProbe/           # BepInEx/Harmony passive logger for copied client
  experiments/
    configs/
    reports/
```

The runtime-only output remains in `_runtime`, not in source folders.

## Automation phases

### Phase 1: offline truth model

Goal: prove whether a strategy is positive EV in simulation before touching the
live game loop.

Tasks:

- Parse the nyukyu tables from assets.
- Implement the JanQ state transition model:
  - 13-tile hand before shot.
  - choose area 1-7.
  - sample/resolve draw tile.
  - apply fourth-copy +1 ball behavior.
  - 14-tile discard/agari decision.
  - stop on agari or no balls.
- Replace `mahjong-helper.exe` with an embedded Python or C# mahjong evaluator.
- Start with ordinary shanten/wait-count policies, then add han/odds-aware EV.
- Use the live `RecvConfigOdds` values once collected; until then, keep odds as
  configurable experimental input.

Success criteria:

- Area probability tests pass exactly against parsed tables.
- Simulator reproduces simple hand examples.
- Baseline/public strategy has measured EV and confidence interval.

### Phase 2: passive client probe

Goal: record the real game without playing automatically.

Preferred implementation:

- Install a fresh BepInEx 5 Mono setup into
  `sega_net_MJ\MJ` in the copied client only.
- Build `JanqProbe.dll` against the copied game's managed assemblies.
- Use Harmony postfixes on:
  - `JanQAPI.RecvConfigOdds`
  - `JanQAPI.RecvGameHaipai`
  - `JanQAPI.RecvGameTsumo`
  - `JanQAPI.RecvActDahai`
  - `JanQAPI.RecvJanQResult`
  - optionally `GameManager.MakeShotArea`, `MakeShotEntry`, `SetNyukyuBlack`
- Emit JSONL to `_runtime\logs\janq_events.jsonl`.

Event schema sketch:

```json
{"type":"haipai","ts":"...","mode":"NORMAL","zandan":8,"tehai":[...],"dora":...,"ura":...}
{"type":"shot","ts":"...","area":4}
{"type":"tsumo","ts":"...","area":4,"pai":31,"zandan":7,"replay":false,"tehai":[...]}
{"type":"dahai","ts":"...","richi":false,"pos":3,"sutehai":12}
{"type":"result","ts":"...","win":1,"han":6,"yakuLevel":"YL_06HAN","odds":...,"delta":...}
```

Success criteria:

- A manually played minimum-bet session can be reconstructed completely from
  JSONL.
- Observed area->tile frequencies are statistically consistent with parsed
  tables.
- `zandan` transitions verify the fourth-copy rule.

### Phase 3: recommendation assistant

Goal: before full automation, make the bot recommend actions and let the human
confirm.

Tasks:

- A small console or local web panel tails `janq_events.jsonl`.
- It prints:
  - current hand
  - best area
  - expected value estimate
  - suggested discard/reach/agari
  - confidence/why
- Use this to compare model recommendations against actual gameplay.

Success criteria:

- Human can play from recommendations without desync.
- Model and client agree on win/discard legality.

### Phase 4: controlled active automation

Goal: execute the same policy with hard limits.

Preferred execution order:

1. UI automation through the copied client: safer to reason about and closer to
   normal user actions.
2. Plugin-assisted click/gauge calibration only if UI automation is too flaky.
3. Direct `JanQAPI.sendAction*` calls only for local controlled experiments,
   not as the default live-play path.

Guardrails:

- Minimum bet until EV is proven with live data.
- Stop-loss, stop-win, max-hands, max-runtime.
- Manual kill switch.
- Full JSONL logs for every action and result.
- No action when state confidence is incomplete.

Success criteria:

- 100+ minimum-bet hands with no state desync.
- The live mean delta and confidence interval match simulation within expected
  variance.

### Phase 5: EV proof

Goal: answer the actual question: does this strategy increase chips?

Experiment design:

- Define net delta per hand as final currency change minus bet/input cost.
- Separate normal/challenge/bonus/free-play samples.
- Record bet level and currency type.
- Use sequential confidence intervals:
  - stop early only if lower 95% CI is above zero, or if upper 95% CI is below
    zero.
- Compare at least three policies:
  - public heuristic
  - shanten/wait greedy
  - EV search policy

The final criterion is not "it wins often"; it is "expected net chips per hand
is positive after variance and mode bonuses are included."

## Immediate next implementation steps

1. Create the new `janq_lab` package skeleton.
2. Add a tested parser for `nyukyu_*_table.bytes`.
3. Add typed tile/hand utilities and shanten evaluator.
4. Add a first Monte Carlo simulator with the public heuristic strategy.
5. Install BepInEx only into the copied client and build the passive
   `JanqProbe` logger.
6. Run a minimum-bet manual capture and compare empirical tables with asset
   tables.

## Implementation progress

The fresh rebuild has started under `src/janq_lab`.

Completed:

- `janq_lab.tiles`
  - canonical 0-33 tile ids and display names.
- `janq_lab.assets.nyukyu`
  - parses 34 x 7 little-endian uint16 nyukyu tables.
  - validates each area sum is 10000.
  - confirms the three current copied-client nyukyu tables share the same
    SHA256.
  - provides area descriptions and weighted draws.
- `janq_lab.assets.special`
  - parses paren/challenge and yakuman setup tables.
  - preserves unknown doukei tables as raw values for later interpretation.
- `janq_lab.model.hand`
  - count-based tile set.
  - standard, chiitoitsu, and kokushi shanten checks.
  - complete-hand detection.
  - winning-tile and improving-tile enumeration.
  - simple best-discard candidates.
- `janq_lab.strategy.greedy`
  - a first baseline that chooses the area with the most target weight for
    winning or improving tiles.
  - a simple discard policy based on shanten and accept count.
- `janq_lab.model.simulator`
  - first offline normal-hand loop:
    13-tile hand -> choose area -> draw -> fourth-copy refund -> agari/discard.
  - includes replay and max-turn guards for experiments.
- `plugin/JanqProbe`
  - BepInEx 5 passive logger plugin for the copied client.
  - hooks key JanQ action and receive methods.
  - writes JSONL into `_runtime\logs\janq_events.jsonl`.
  - was compiled and smoke-tested against the copied `MJ.exe`.
- `janq_lab.probe.events`
  - reads and validates `JanqProbe` JSONL events for later replay and EV
    analysis.
- `janq_lab.probe.normalize`
  - converts raw JanQAPI 1-based tile ids into the model's 0-based ids.
  - filters API placeholders such as `0` and `9999`.
- `janq_lab.probe.replay`
  - reconstructs coarse observed hands from `JanqProbe` JSONL.
  - pairs `send_action_shot` with later `recv_game_tsumo`.
  - carries start/end currency snapshots for observed delta summaries.
- `janq_lab.analysis.monte_carlo`
  - runs batch simulations for the current strategy baselines.
  - default strategy is the fast public攻略 baseline.
  - reports win rate, Wilson 95% interval, average shots, and average
    fourth-copy refunds.
- `janq_lab.analysis.observed_ev`
  - summarizes real captured currency deltas with a mean and 95% interval.
- `janq_lab.analysis.shot_distribution`
  - compares observed `area -> tile` outcomes against the parsed nyukyu table.
  - reports per-area shot counts, impossible observations, chi-square, and top
    observed tiles.

Verification command:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
python -m janq_lab.assets.nyukyu
python -m janq_lab.assets.special
dotnet build plugin\JanqProbe\JanqProbe.csproj -c Release
python -m janq_lab.analysis.monte_carlo --hands 1000 --seed 20260612 --strategy public
python -m janq_lab.probe.replay _runtime\logs\janq_events.jsonl
python -m janq_lab.analysis.shot_distribution _runtime\logs\janq_events.jsonl
python -m janq_lab.analysis.observed_ev _runtime\logs\janq_events.jsonl --currency mjchip
```

Current verified result:

- 31 unit tests pass.
- The command-line table dump matches the reverse-engineered area
  distributions.
- `JanqProbe.dll` builds without warnings or errors and is copied to
  `sega_net_MJ\MJ\BepInEx\plugins`.
- BepInEx 5.4.23.5 x64 loads in the copied client and emits
  `probe_loaded/probe_unloaded`.
- Current public-baseline simplified Monte Carlo:
  - 1000 hands
  - seed `20260612`
  - wins `62`
  - win rate `0.062`
  - Wilson 95% CI `[0.04866418011998064, 0.07868806031336564]`
  - average shots `8.442`
  - average fourth-copy refunds `0.52`
- Current observed EV summary has `hands: 0`, because no real JanQ hand has
  been captured yet.
- Current shot-distribution summary has `total_shots: 0`, because no real
  `send_action_shot`/`recv_game_tsumo` pairs have been captured yet.
- Computer Use currently fails during bootstrap with a local runtime package
  export error recorded in `_runtime\logs\tooling.log`; this does not block the
  passive logger or offline analysis path.

Important limitations:

- The simulator is not yet an EV proof.
- It does not yet use live server odds from `RecvConfigOdds`.
- It does not yet model challenge/bonus/free-play transitions.
- It uses a deliberately simple greedy baseline, not the final optimal policy.
- Initial hand generation still needs to be validated against real captured
  `RecvGameHaipai` data.
- No real JanQ gameplay sample has been captured yet. The next required
  evidence is a minimum-bet manual session that produces `recv_game_haipai`,
  `recv_game_tsumo`, `recv_config_odds`, and `recv_janq_result` events.
- The public-baseline simulation is deliberately simplified. It should be used
  as a baseline and performance check, not as a proof of profitability.
