# JanQ rules and economy model

This document records the rule/economy model used by `janq_lab` simulations.

## Sources

- Official help page: `https://pl.sega-mj.com/casino_help_view/helpJanQIndex`
- Official payout formula image:
  `/images/mjm/casino_help/janq/janq_img_11.jpg`
- Official yakuman challenge formula image:
  `/images/mjm/casino_help/janq/janq_img_12.jpg`
- Copied client tables:
  `sega_net_MJ/MJ/MJ_Data/StreamingAssets/Janq/table`
- Decompiled client API/result fields:
  `_runtime/dumps/decompiled_janq`

## Game flow

1. Normal game starts after paying one BET.
2. The normal game starts with 13 tiles, 8 balls, one dora tile, and one ura-dora
   tile supplied by the server.
3. For each shot, the selected area determines the draw distribution. The copied
   client has 7 areas and the same nyukyu distribution for normal, hachiren
   challenge, and yakuman challenge.
4. Drawing a tile already held as three copies refunds one ball.
5. If no balls remain without a win, the session ends and only the initial BET
   was spent.
6. A normal non-yakuman win pays immediately and enters hachiren challenge.
7. A normal yakuman or counted-yakuman win pays immediately and enters yakuman
   challenge.
8. Hachiren challenge is free play, starts from tenpai, has 3 balls, auto-wins
   when a winning tile is drawn, and continues on wins. Reaching eight wins
   including the normal-game win enters yakuman challenge. A counted yakuman
   before eight also enters yakuman challenge.
9. Yakuman challenge is free play, starts from yakuman tenpai, has 3 balls, and
   continues on yakuman wins. Ryukyoku ends the session.

## Adopted JanQ scoring rules

- No calls, no kan, no red dora, no fu.
- East round is fixed. East, white, green, and red dragon are value tiles.
- South, West, and North are not value-tile yaku by themselves.
- Reach, double reach, ippatsu, and ura-dora are normal-game-only effects.
- First draw in hachiren/yakuman challenge does not award tenhou or ippatsu.
- 13+ han is counted yakuman.
- Natural yakuman can overlap up to four yakuman units.
- Counted yakuman and natural yakuman do not overlap.
- Kokushi 13-sided, suuankou tanki, daisuushi, and pure chuuren are not double
  yakuman in JanQ.

## Official payout table

Main game and hachiren challenge:

| Result | Multiplier |
| --- | ---: |
| 1 han | 0.2x |
| 2 han | 0.4x |
| 3 han | 0.6x |
| 4-5 han / mangan | 1.0x |
| 6-7 han / haneman | 1.5x |
| 8-10 han / baiman | 2.0x |
| 11-12 han / sanbaiman | 3.0x |
| 13+ han / counted yakuman | 10.0x |
| yakuman | 10.0x |
| double yakuman | 20.0x |
| triple yakuman | 30.0x |
| quadruple yakuman | 40.0x |

Formula:

```text
payout = BET * result_multiplier
```

Yakuman challenge:

```text
payout = BET * yakuman_multiplier(10x) * cumulative_yakuman_win_count
```

The official help notes that the yakuman win count accumulates until ryukyoku,
and overlapping yakuman add their overlapping units to that count.

## Current simulation assumptions

- Normal initial hands default to a physical 136-tile wall baseline, but this is
  only a placeholder until enough `recv_game_haipai` samples are captured. The
  client receives normal haipai from the server, so real play may use a shaped
  distribution.
- Economy simulation can instead bootstrap normal haipai from captured probe
  logs with `--normal-haipai-source observed --observed-events <jsonl>`.
- Normal dora and ura-dora tile types are sampled uniformly from the 34 JanQ
  tile ids.
- Shot draws use the copied client's official nyukyu tables.
- Hachiren and yakuman challenge starting hands use the copied client's special
  tables.
- `previous_han` hachiren mode chooses `paren_N_table.bytes` from the previous
  hand's han clamped to 2..12.
- `select_table` hachiren mode samples `paren_N_table.bytes` from
  `paren_select_table.bytes`.
- `route_ev` can declare reach in normal mode. After reach, the hand is locked:
  it keeps the tenpai shape, discards drawn non-winning tiles, can score
  ippatsu on the first draw after reach, and counts ura-dora on a win.

## Commands

```powershell
$env:PYTHONPATH='src'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m janq_lab.analysis.economy_monte_carlo --sessions 50000 --seed 20260612 --bet 10 --strategy public --paren-table-mode previous_han
python -m janq_lab.analysis.economy_monte_carlo --sessions 20000 --seed 20260612 --bet 10 --strategy public --paren-table-mode select_table
python -m janq_lab.analysis.haipai_distribution _runtime\logs\janq_events.jsonl --baseline-hands 10000
```

Results are stored in:

- `_runtime/logs/economy_public_50k_previous_han.json`
- `_runtime/logs/economy_public_20k_select_table.json`
