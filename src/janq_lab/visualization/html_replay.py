"""Generate a self-contained HTML replay for one simulated JanQ hand."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
import argparse
import random
from typing import Any, Callable

from janq_lab.assets.nyukyu import AREA_COUNT, NyukyuTable, load_tables
from janq_lab.model.hand import TileSet, tile_set
from janq_lab.model.haipai import random_wall_hand
from janq_lab.model.scoring import JanqScore, score_hand
from janq_lab.strategy.greedy import (
    AreaDecision,
    DiscardDecision,
    choose_greedy_area,
    choose_greedy_discard,
)
from janq_lab.strategy.public import choose_public_area, choose_public_discard
from janq_lab.strategy.route_ev import choose_route_ev_area, choose_route_ev_discard
from janq_lab.tiles import TILE_NAMES, tile_name


ChooseArea = Callable[..., AreaDecision]
ChooseDiscard = Callable[..., DiscardDecision]


@dataclass(frozen=True)
class ReplayTurn:
    turn: int
    hand_before: tuple[int, ...]
    balls_before: int
    area_decision: AreaDecision
    drawn_tile: int
    hand_after_draw: tuple[int, ...]
    balls_after_draw: int
    fourth_copy: bool
    replays: int
    discard_decision: DiscardDecision
    hand_after_discard: tuple[int, ...]

    @property
    def is_agari(self) -> bool:
        return self.discard_decision.is_agari


@dataclass(frozen=True)
class ReplayHand:
    seed: int
    strategy: str
    initial_hand: tuple[int, ...]
    turns: tuple[ReplayTurn, ...]
    final_hand: tuple[int, ...]
    win: bool
    score: JanqScore | None
    dora_id: int | None


def simulate_replay(
    *,
    seed: int = 1,
    strategy: str = "route_ev",
    balls: int = 8,
    initial_hand: tuple[int, ...] | None = None,
    max_turns: int = 100,
) -> ReplayHand:
    if balls < 1:
        raise ValueError("balls must be positive")
    if max_turns < 1:
        raise ValueError("max_turns must be positive")

    rng = random.Random(seed)
    table = load_tables()["nyukyu_base_table.bytes"]
    choose_area, choose_discard = _strategy_functions(strategy)
    hand = tile_set(initial_hand) if initial_hand is not None else random_wall_hand(rng)
    if hand.size != 13:
        raise ValueError(f"initial hand must have 13 tiles, got {hand.size}")
    original = hand.to_tiles()
    dora_id = rng.randrange(34)

    turns: list[ReplayTurn] = []
    current_balls = balls
    for turn_number in range(1, max_turns + 1):
        if current_balls <= 0:
            break
        hand_before = hand.to_tiles()
        area_decision = _call_choose_area(choose_area, hand, table, current_balls)
        balls_before = current_balls
        current_balls -= 1

        replays = 0
        while True:
            drawn_tile = table.draw(area_decision.area, rng)
            if hand.can_add(drawn_tile):
                break
            replays += 1
            if replays > 100:
                raise RuntimeError("too many impossible draw replays")

        fourth_copy = hand.counts[drawn_tile] == 3
        hand = hand.with_added(drawn_tile)
        if fourth_copy:
            current_balls += 1
        hand_after_draw = hand.to_tiles()
        balls_after_draw = current_balls
        discard_decision = _call_choose_discard(choose_discard, hand, current_balls)

        if discard_decision.is_agari:
            turns.append(
                ReplayTurn(
                    turn=turn_number,
                    hand_before=hand_before,
                    balls_before=balls_before,
                    area_decision=area_decision,
                    drawn_tile=drawn_tile,
                    hand_after_draw=hand_after_draw,
                    balls_after_draw=balls_after_draw,
                    fourth_copy=fourth_copy,
                    replays=replays,
                    discard_decision=discard_decision,
                    hand_after_discard=hand.to_tiles(),
                )
            )
            score = score_hand(hand, dora_id=dora_id)
            return ReplayHand(
                seed=seed,
                strategy=strategy,
                initial_hand=original,
                turns=tuple(turns),
                final_hand=hand.to_tiles(),
                win=True,
                score=score,
                dora_id=dora_id,
            )

        if discard_decision.discard_tile is None:
            raise RuntimeError("discard decision did not include a tile")
        hand = hand.with_removed_one(discard_decision.discard_tile)
        turns.append(
            ReplayTurn(
                turn=turn_number,
                hand_before=hand_before,
                balls_before=balls_before,
                area_decision=area_decision,
                drawn_tile=drawn_tile,
                hand_after_draw=hand_after_draw,
                balls_after_draw=balls_after_draw,
                fourth_copy=fourth_copy,
                replays=replays,
                discard_decision=discard_decision,
                hand_after_discard=hand.to_tiles(),
            )
        )

    return ReplayHand(
        seed=seed,
        strategy=strategy,
        initial_hand=original,
        turns=tuple(turns),
        final_hand=hand.to_tiles(),
        win=False,
        score=None,
        dora_id=dora_id,
    )


def render_replay_html(replay: ReplayHand) -> str:
    title = f"JanQ replay seed {replay.seed}"
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{escape(title)}</title>",
            f"<style>{_CSS}</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            _render_header(replay),
            _render_strategy_note(replay.strategy),
            _render_hand_panel("起手", replay.initial_hand, aside=f"Dora: {_tile_label(replay.dora_id)}"),
            _render_turns(replay),
            _render_result(replay),
            "</main>",
            "</body>",
            "</html>",
        )
    )


def write_replay_html(replay: ReplayHand, output: str | Path) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_replay_html(replay), encoding="utf-8")
    return path


def _render_header(replay: ReplayHand) -> str:
    result = "和牌" if replay.win else "流局/未和"
    return f"""
<section class="hero">
  <div>
    <p class="eyebrow">JanQ Offline Replay</p>
    <h1>当前策略：{escape(replay.strategy)}</h1>
    <p class="subtle">seed={replay.seed} · 回合数={len(replay.turns)} · 结果={result}</p>
  </div>
  <div class="status {('win' if replay.win else 'lose')}">{result}</div>
</section>
"""


def _render_strategy_note(strategy: str) -> str:
    if strategy == "route_ev":
        body = (
            "普通局优先检索役满路线：四暗刻、大三元、九莲、国士；"
            "役满路线激活后，选区按前进概率/和牌概率/保护概率，不按短期 EV；"
            "没有明确役满路线时回退到 public 路线。奖励局使用 3 球听牌专用策略。"
        )
    elif strategy == "public":
        body = "public 策略：有听牌先打最大和牌概率区；否则优先三元或最多花色路线。"
    else:
        body = "greedy 策略：有听牌先追和牌；否则追最大向听改良。"
    return f"""
<section class="panel">
  <h2>策略摘要</h2>
  <p>{escape(body)}</p>
</section>
"""


def _render_hand_panel(title: str, tiles: tuple[int, ...], *, aside: str = "") -> str:
    aside_html = f'<span class="aside">{escape(aside)}</span>' if aside else ""
    return f"""
<section class="panel">
  <h2>{escape(title)}{aside_html}</h2>
  <div class="tiles">{''.join(_tile_html(tile_id) for tile_id in tiles)}</div>
</section>
"""


def _render_turns(replay: ReplayHand) -> str:
    items = []
    for turn in replay.turns:
        discard = turn.discard_decision
        discard_text = "胡牌" if discard.is_agari else f"弃 {_tile_label(discard.discard_tile)}"
        protection = "保护 +1 球" if turn.fourth_copy else "无保护"
        items.append(
            f"""
<article class="turn">
  <div class="turn-head">
    <h3>第 {turn.turn} 球</h3>
    <div class="balls">球数 {turn.balls_before} → {turn.balls_after_draw}</div>
  </div>
  {_area_bar(turn.area_decision.area)}
  <div class="meta">
    <span>区域理由：{escape(turn.area_decision.reason)}</span>
    <span>目标权重：{turn.area_decision.target_weight} / 10000</span>
    <span>{protection}</span>
    <span>重抽：{turn.replays}</span>
  </div>
  <div class="grid">
    <section>
      <h4>射击前</h4>
      <div class="tiles small">{''.join(_tile_html(tile_id) for tile_id in turn.hand_before)}</div>
    </section>
    <section>
      <h4>摸到</h4>
      <div class="drawn">{_tile_html(turn.drawn_tile, extra='draw')}</div>
    </section>
    <section>
      <h4>决策</h4>
      <p class="decision">{escape(discard_text)}</p>
      <p class="reason">{escape(discard.reason)}</p>
    </section>
    <section>
      <h4>回合后</h4>
      <div class="tiles small">{''.join(_tile_html(tile_id) for tile_id in turn.hand_after_discard)}</div>
    </section>
  </div>
</article>
"""
        )
    return f'<section class="timeline">{"".join(items)}</section>'


def _render_result(replay: ReplayHand) -> str:
    if replay.score is None:
        score = "未和牌"
    else:
        yaku = ", ".join(replay.score.yaku) or "无"
        score = f"{replay.score.yaku_level} · han={replay.score.han} · yakuman={replay.score.yakuman_count} · {yaku}"
    return f"""
<section class="panel result-panel">
  <h2>最终</h2>
  <div class="tiles">{''.join(_tile_html(tile_id) for tile_id in replay.final_hand)}</div>
  <p>{escape(score)}</p>
</section>
"""


def _area_bar(active: int) -> str:
    cells = []
    for area in range(1, AREA_COUNT + 1):
        cls = "area active" if area == active else "area"
        cells.append(f'<span class="{cls}">{area}</span>')
    return f'<div class="areas">{"".join(cells)}</div>'


def _tile_html(tile_id: int | None, *, extra: str = "") -> str:
    if tile_id is None:
        return '<span class="tile blank">?</span>'
    label = _tile_label(tile_id)
    suit = _tile_class(tile_id)
    classes = " ".join(part for part in ("tile", suit, extra) if part)
    return f'<span class="{classes}" title="{escape(tile_name(tile_id))}">{escape(label)}</span>'


def _tile_label(tile_id: int | None) -> str:
    if tile_id is None:
        return "?"
    if 0 <= tile_id <= 8:
        return f"{tile_id + 1}万"
    if 9 <= tile_id <= 17:
        return f"{tile_id - 8}索"
    if 18 <= tile_id <= 26:
        return f"{tile_id - 17}饼"
    return {
        27: "东",
        28: "南",
        29: "西",
        30: "北",
        31: "白",
        32: "发",
        33: "中",
    }.get(tile_id, TILE_NAMES[tile_id])


def _tile_class(tile_id: int) -> str:
    if tile_id < 9:
        return "man"
    if tile_id < 18:
        return "sou"
    if tile_id < 27:
        return "pin"
    if tile_id >= 31:
        return "dragon"
    return "wind"


def _strategy_functions(strategy: str) -> tuple[ChooseArea, ChooseDiscard]:
    if strategy == "public":
        return choose_public_area, choose_public_discard
    if strategy == "greedy":
        return choose_greedy_area, choose_greedy_discard
    if strategy == "route_ev":
        return choose_route_ev_area, choose_route_ev_discard
    raise ValueError(f"unknown strategy: {strategy}")


def _call_choose_area(
    choose_area: ChooseArea,
    hand: TileSet,
    table: NyukyuTable,
    balls: int,
) -> AreaDecision:
    if getattr(choose_area, "uses_context", False):
        return choose_area(hand, table, balls)
    return choose_area(hand, table)


def _call_choose_discard(
    choose_discard: ChooseDiscard,
    hand: TileSet,
    balls: int,
) -> DiscardDecision:
    if getattr(choose_discard, "uses_context", False):
        return choose_discard(hand, balls)
    return choose_discard(hand)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate a JanQ simulated hand HTML replay.")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--strategy", choices=("public", "greedy", "route_ev"), default="route_ev")
    parser.add_argument("--balls", type=int, default=8)
    parser.add_argument("--max-turns", type=int, default=100)
    parser.add_argument(
        "--output",
        default=None,
        help="Output HTML path. Defaults to _runtime/replays/janq_replay_<seed>.html",
    )
    args = parser.parse_args(argv)

    replay = simulate_replay(
        seed=args.seed,
        strategy=args.strategy,
        balls=args.balls,
        max_turns=args.max_turns,
    )
    output = args.output or f"_runtime/replays/janq_replay_{args.strategy}_{args.seed}.html"
    path = write_replay_html(replay, output)
    print(path)


_CSS = r"""
:root {
  color-scheme: light;
  --ink: #1d2630;
  --muted: #6f7782;
  --line: #d7dde5;
  --panel: #ffffff;
  --bg: #f3f5f7;
  --accent: #2d6cdf;
  --good: #188b54;
  --bad: #a23b3b;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
}
.shell {
  width: min(1180px, calc(100vw - 32px));
  margin: 0 auto;
  padding: 28px 0 48px;
}
.hero {
  display: flex;
  justify-content: space-between;
  align-items: end;
  gap: 24px;
  padding: 22px 0 18px;
  border-bottom: 1px solid var(--line);
}
.eyebrow { margin: 0 0 4px; color: var(--accent); font-weight: 700; font-size: 13px; }
h1 { margin: 0; font-size: 34px; letter-spacing: 0; }
h2, h3, h4 { letter-spacing: 0; }
.subtle { margin: 8px 0 0; color: var(--muted); }
.status {
  min-width: 86px;
  text-align: center;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px 14px;
  background: var(--panel);
  font-weight: 700;
}
.status.win { color: var(--good); }
.status.lose { color: var(--bad); }
.panel, .turn {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  margin-top: 18px;
  padding: 18px;
}
.panel h2 { margin: 0 0 12px; font-size: 20px; }
.aside { margin-left: 12px; color: var(--muted); font-size: 14px; font-weight: 400; }
.tiles {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  align-items: center;
}
.tiles.small { gap: 5px; }
.tile {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 58px;
  border: 1px solid #b8c0ca;
  border-radius: 6px;
  background: linear-gradient(180deg, #fff 0%, #f3f5f7 100%);
  box-shadow: 0 2px 0 #c3cad3;
  font-weight: 800;
  font-size: 16px;
}
.tiles.small .tile { width: 34px; height: 48px; font-size: 13px; border-radius: 5px; }
.tile.man { color: #b73535; }
.tile.sou { color: #17834b; }
.tile.pin { color: #2859ba; }
.tile.wind { color: #26313d; }
.tile.dragon { color: #8f2ab4; }
.tile.draw {
  width: 56px;
  height: 76px;
  font-size: 20px;
  border-color: #e0a526;
  box-shadow: 0 3px 0 #c98d16;
}
.turn-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.turn h3 { margin: 0; font-size: 20px; }
.balls { color: var(--muted); font-weight: 700; }
.areas {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 6px;
  margin: 14px 0;
}
.area {
  text-align: center;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 7px 0;
  color: var(--muted);
  background: #f8fafc;
  font-weight: 700;
}
.area.active {
  color: #fff;
  border-color: var(--accent);
  background: var(--accent);
}
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  color: var(--muted);
  font-size: 13px;
}
.meta span {
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 5px 9px;
  background: #f8fafc;
}
.grid {
  display: grid;
  grid-template-columns: 2fr 0.6fr 1fr 2fr;
  gap: 14px;
  margin-top: 16px;
}
.grid section {
  min-width: 0;
  border-left: 3px solid #edf0f4;
  padding-left: 10px;
}
.grid h4 { margin: 0 0 8px; font-size: 14px; color: var(--muted); }
.decision { margin: 0; font-size: 18px; font-weight: 800; }
.reason { margin: 6px 0 0; color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
.result-panel p { color: var(--muted); }
@media (max-width: 760px) {
  .hero { align-items: start; flex-direction: column; }
  h1 { font-size: 27px; }
  .grid { grid-template-columns: 1fr; }
}
"""


if __name__ == "__main__":
    main()
