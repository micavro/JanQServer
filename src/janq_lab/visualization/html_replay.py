"""Generate self-contained HTML replay dashboards for JanQ simulations."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
import argparse
import base64
from io import BytesIO
import json
import os
import random
from typing import Callable

from janq_lab.assets.nyukyu import (
    AREA_COUNT,
    EXPECTED_WEIGHT_SUM,
    NyukyuTable,
    load_tables,
)
from janq_lab.model.haipai import load_observed_normal_haipai, random_wall_hand
from janq_lab.model.hand import TileSet, tile_set
from janq_lab.model.scoring import JanqScore, score_hand
from janq_lab.strategy.greedy import (
    AreaDecision,
    DiscardDecision,
    choose_greedy_area,
    choose_greedy_discard,
)
from janq_lab.strategy.public import choose_public_area, choose_public_discard
from janq_lab.strategy.route_ev import choose_route_ev_area, choose_route_ev_discard
from janq_lab.tiles import TILE_COUNT, TILE_NAMES, tile_name


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
    source_label: str = "随机起手"


@dataclass(frozen=True)
class ReplaySet:
    seed: int
    strategy: str
    replays: tuple[ReplayHand, ...]
    source_label: str
    table_name: str
    observed_hand_count: int = 0


@dataclass(frozen=True)
class TileImageAssets:
    tile_urls: tuple[str | None, ...]
    source_label: str

    @property
    def available(self) -> bool:
        return any(self.tile_urls)


TILE_ATLAS_BOXES: tuple[tuple[int, int, int, int], ...] = (
    (22, 18, 130, 166),
    (145, 20, 253, 166),
    (268, 18, 376, 166),
    (391, 16, 499, 166),
    (505, 10, 632, 166),
    (760, 10, 885, 166),
    (885, 10, 1005, 166),
    (20, 178, 130, 340),
    (145, 176, 255, 340),
    (510, 342, 635, 510),
    (635, 342, 755, 510),
    (755, 342, 885, 510),
    (20, 512, 135, 680),
    (140, 512, 260, 680),
    (265, 512, 382, 680),
    (385, 512, 510, 680),
    (510, 512, 635, 680),
    (635, 512, 755, 680),
    (260, 178, 386, 340),
    (395, 178, 490, 340),
    (510, 178, 632, 340),
    (630, 178, 755, 340),
    (755, 178, 884, 340),
    (20, 342, 135, 510),
    (140, 342, 260, 510),
    (265, 342, 382, 510),
    (385, 342, 510, 510),
    (755, 512, 885, 680),
    (865, 512, 1000, 680),
    (20, 690, 135, 835),
    (140, 690, 260, 835),
    (640, 690, 755, 835),
    (390, 690, 510, 835),
    (510, 690, 635, 835),
)


def simulate_replay(
    *,
    seed: int = 1,
    strategy: str = "route_ev",
    balls: int = 8,
    initial_hand: tuple[int, ...] | None = None,
    max_turns: int = 100,
    source_label: str = "随机起手",
    table: NyukyuTable | None = None,
) -> ReplayHand:
    if balls < 1:
        raise ValueError("balls must be positive")
    if max_turns < 1:
        raise ValueError("max_turns must be positive")

    rng = random.Random(seed)
    table = table or load_tables()["nyukyu_base_table.bytes"]
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
                source_label=source_label,
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
        source_label=source_label,
    )


def simulate_replay_set(
    *,
    seed: int = 1,
    strategy: str = "route_ev",
    examples: int = 100,
    balls: int = 8,
    max_turns: int = 100,
    source: str = "random",
    events_path: str | Path | None = None,
) -> ReplaySet:
    if examples < 1:
        raise ValueError("examples must be positive")
    if source not in {"random", "observed", "auto"}:
        raise ValueError("source must be random, observed, or auto")

    table = load_tables()["nyukyu_base_table.bytes"]
    observed_hands: tuple[TileSet, ...] = ()
    source_label = "随机生成起手"

    if source in {"observed", "auto"}:
        if events_path is not None and Path(events_path).is_file():
            observed = load_observed_normal_haipai(events_path)
            observed_hands = observed.hands
            if observed_hands:
                source_label = (
                    f"probe日志起手：{observed.source}，"
                    f"NORMAL 13枚 {len(observed_hands)} 个"
                )
            elif source == "observed":
                raise ValueError(f"no NORMAL 13-tile hands found in {events_path}")
        elif source == "observed":
            raise FileNotFoundError(f"events file not found: {events_path}")

    replays: list[ReplayHand] = []
    for index in range(examples):
        initial_hand = None
        hand_label = f"随机起手 #{index + 1}"
        if observed_hands:
            observed_index = index % len(observed_hands)
            initial_hand = observed_hands[observed_index].to_tiles()
            hand_label = f"日志起手 #{observed_index + 1}"

        replays.append(
            simulate_replay(
                seed=seed + index,
                strategy=strategy,
                balls=balls,
                initial_hand=initial_hand,
                max_turns=max_turns,
                source_label=hand_label,
                table=table,
            )
        )

    return ReplaySet(
        seed=seed,
        strategy=strategy,
        replays=tuple(replays),
        source_label=source_label,
        table_name=table.name,
        observed_hand_count=len(observed_hands),
    )


def render_replay_html(
    replay: ReplayHand,
    *,
    resource_dir: str | Path | None = None,
    output_path: str | Path | None = None,
) -> str:
    return render_replay_set_html(
        ReplaySet(
            seed=replay.seed,
            strategy=replay.strategy,
            replays=(replay,),
            source_label=replay.source_label,
            table_name="nyukyu_base_table.bytes",
            observed_hand_count=0,
        ),
        resource_dir=resource_dir,
        output_path=output_path,
    )


def render_replay_set_html(
    replay_set: ReplaySet,
    *,
    resource_dir: str | Path | None = None,
    output_path: str | Path | None = None,
) -> str:
    table = load_tables()[replay_set.table_name]
    assets = discover_tile_image_assets(resource_dir=resource_dir, output_path=output_path)
    title = f"JanQ replay dashboard seed {replay_set.seed}"
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{escape(title)}</title>",
            f"<style>{_CSS}{_asset_css(assets)}</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            _render_header(replay_set, assets),
            _render_strategy_note(replay_set.strategy),
            '<section class="workspace">',
            _render_example_list(replay_set),
            '<div class="replay-stage">',
            "".join(
                _render_replay_card(replay, index, table)
                for index, replay in enumerate(replay_set.replays)
            ),
            "</div>",
            "</section>",
            "</main>",
            f"<script>{_JS}</script>",
            "</body>",
            "</html>",
        )
    )


def write_replay_html(
    replay: ReplayHand,
    output: str | Path,
    *,
    resource_dir: str | Path | None = None,
) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_replay_html(replay, resource_dir=resource_dir, output_path=path),
        encoding="utf-8",
    )
    return path


def write_replay_set_html(
    replay_set: ReplaySet,
    output: str | Path,
    *,
    resource_dir: str | Path | None = None,
) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_replay_set_html(replay_set, resource_dir=resource_dir, output_path=path),
        encoding="utf-8",
    )
    return path


def discover_tile_image_assets(
    *,
    resource_dir: str | Path | None = None,
    output_path: str | Path | None = None,
) -> TileImageAssets:
    resource_path = _resolve_resource_dir(resource_dir, output_path)
    if resource_path is None:
        return TileImageAssets((None,) * TILE_COUNT, "CSS文字牌")

    atlas_path = resource_path / "color00.png"
    if not atlas_path.is_file():
        return TileImageAssets((None,) * TILE_COUNT, f"未找到 {atlas_path.name}")

    try:
        from PIL import Image
    except Exception:
        return TileImageAssets((None,) * TILE_COUNT, "Pillow不可用，使用CSS文字牌")

    try:
        with Image.open(atlas_path) as atlas:
            image = atlas.convert("RGBA")
            urls = tuple(_crop_tile_data_url(image, box) for box in TILE_ATLAS_BOXES)
    except Exception as exc:
        return TileImageAssets((None,) * TILE_COUNT, f"{atlas_path.name}读取失败：{exc}")

    if len(urls) != TILE_COUNT:
        return TileImageAssets((None,) * TILE_COUNT, "牌图数量不完整")
    return TileImageAssets(urls, f"{atlas_path.as_posix()} / color00")


def _resolve_resource_dir(
    resource_dir: str | Path | None,
    output_path: str | Path | None,
) -> Path | None:
    candidates: list[Path] = []
    if resource_dir is not None:
        candidates.append(Path(resource_dir))
    env_dir = os.environ.get("JANQ_RESOURCE_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    if output_path is not None:
        out = Path(output_path).resolve()
        candidates.extend(parent / "allresourse" for parent in (out.parent, *out.parents))
    cwd = Path.cwd().resolve()
    candidates.extend(parent / "allresourse" for parent in (cwd, *cwd.parents))

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_dir():
            return resolved
    return None


def _crop_tile_data_url(image: object, box: tuple[int, int, int, int]) -> str:
    crop = image.crop(box)  # type: ignore[attr-defined]
    buffer = BytesIO()
    crop.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _asset_css(assets: TileImageAssets) -> str:
    if not assets.available:
        return ""
    rules = [
        """
.tile.tile-art,
.mini-tile.tile-art {
  background-color: transparent;
  background-position: center;
  background-repeat: no-repeat;
  background-size: contain;
  border-color: transparent;
  box-shadow: none;
  color: transparent;
  text-shadow: none;
}

.tile.tile-art.discarded,
.mini-tile.tile-art.discarded {
  border-color: #d55050;
  box-shadow: 0 0 0 2px rgba(197, 73, 73, 0.28);
  filter: saturate(1.08) brightness(0.96);
}
"""
    ]
    for tile_id, url in enumerate(assets.tile_urls):
        if url is None:
            continue
        safe_url = url.replace(")", "%29")
        rules.append(f".tile-id-{tile_id} {{ background-image: url({safe_url}); }}")
    return "\n".join(rules)


def _render_header(replay_set: ReplaySet, assets: TileImageAssets) -> str:
    wins = sum(1 for replay in replay_set.replays if replay.win)
    yakuman = sum(
        1
        for replay in replay_set.replays
        if replay.score is not None and replay.score.yakuman_count
    )
    return f"""
<section class="hero">
  <div>
    <p class="eyebrow">JanQ Offline Replay Dashboard</p>
    <h1>当前策略：{escape(replay_set.strategy)}</h1>
    <p class="subtle">seed={replay_set.seed} · 样本={len(replay_set.replays)} · 胜局={wins} · 役满={yakuman}</p>
    <p class="source">{escape(replay_set.source_label)} · 概率表={escape(replay_set.table_name)} · 牌图={escape(assets.source_label)}</p>
  </div>
  <div class="stat-strip">
    <span><b>{len(replay_set.replays)}</b> 起手</span>
    <span><b>{wins}</b> 和牌</span>
    <span><b>{yakuman}</b> 役满</span>
  </div>
</section>
"""


def _render_strategy_note(strategy: str) -> str:
    if strategy == "route_ev":
        body = (
            "普通局优先检索役满路线：四暗刻、大三元、九莲、国士；"
            "役满路线激活后，区域选择偏向路线前进、直接和牌和第四张保护。"
            "四暗刻/大三元舍牌会额外保留未来最可能继续打的花色和混一色后路。"
            "没有明确役满路线时回到 public 路线。奖励局仍应使用三球听牌专用策略。"
        )
    elif strategy == "public":
        body = (
            "public 策略：有听牌先打最高和牌概率区；否则优先三元牌或最多花色路线，"
            "舍牌时尽量保留当前目标组。"
        )
    else:
        body = "greedy 策略：有听牌先追和牌；否则追最大向听改善。"
    return f"""
<section class="panel strategy-panel">
  <h2>策略摘要</h2>
  <p>{escape(body)}</p>
</section>
"""


def _render_example_list(replay_set: ReplaySet) -> str:
    buttons = []
    for index, replay in enumerate(replay_set.replays):
        result = _result_text(replay)
        active = " active" if index == 0 else ""
        buttons.append(
            f"""
<button class="example-button{active}" type="button" data-replay-index="{index}" onclick="showReplay({index})">
  <span class="example-top">
    <b>#{index + 1}</b>
    <span class="result-pill {('win' if replay.win else 'lose')}">{escape(result)}</span>
  </span>
  <span class="example-hand">{''.join(_mini_tile_html(tile_id) for tile_id in replay.initial_hand)}</span>
  <span class="example-meta">seed={replay.seed} · {len(replay.turns)}球 · {escape(replay.source_label)}</span>
</button>
"""
        )

    return f"""
<aside class="example-panel">
  <div class="example-panel-head">
    <h2>起手样本</h2>
    <span>{len(replay_set.replays)}局</span>
  </div>
  <div class="example-list">
    {''.join(buttons)}
  </div>
</aside>
"""


def _render_replay_card(replay: ReplayHand, index: int, table: NyukyuTable) -> str:
    active = " active" if index == 0 else ""
    return f"""
<section class="replay-card{active}" data-replay-index="{index}">
  <div class="replay-head">
    <div>
      <p class="eyebrow">Example #{index + 1}</p>
      <h2>{escape(_result_text(replay))}</h2>
      <p class="subtle">seed={replay.seed} · {escape(replay.source_label)} · Dora: {_tile_label(replay.dora_id)}</p>
    </div>
    <div class="score-box">{escape(_score_text(replay.score))}</div>
  </div>
  {_render_hand_panel("起手", replay.initial_hand)}
  {_render_turns(replay, table)}
  {_render_hand_panel("回合后", replay.final_hand, aside=_score_text(replay.score))}
</section>
"""


def _render_hand_panel(title: str, tiles: tuple[int, ...], *, aside: str = "") -> str:
    aside_html = f'<span class="aside">{escape(aside)}</span>' if aside else ""
    return f"""
<section class="hand-panel">
  <h3>{escape(title)}{aside_html}</h3>
  <div class="tiles">{''.join(_tile_html(tile_id) for tile_id in tiles)}</div>
</section>
"""


def _render_turns(replay: ReplayHand, table: NyukyuTable) -> str:
    if not replay.turns:
        return '<section class="empty-state">没有产生摸牌回合。</section>'
    return f"""
<section class="turn-list">
  {''.join(_render_turn(turn, table) for turn in replay.turns)}
</section>
"""


def _render_turn(turn: ReplayTurn, table: NyukyuTable) -> str:
    discard = turn.discard_decision
    discard_text = "和牌" if discard.is_agari else f"弃 {_tile_label(discard.discard_tile)}"
    protection = "保护 +1球" if turn.fourth_copy else "无保护"
    accepts = ", ".join(_tile_label(tile_id) for tile_id in discard.accepts) or "无"
    targets = ", ".join(_tile_label(tile_id) for tile_id in turn.area_decision.target_tiles) or "无"
    probability_data = _json_script(_area_probability_data(turn.hand_before, table))

    return f"""
<article class="turn">
  <div class="turn-head">
    <div>
      <h3>第 {turn.turn} 球</h3>
      <p>{escape(turn.area_decision.reason)}</p>
    </div>
    <div class="balls">球数 {turn.balls_before} → {turn.balls_after_draw}</div>
  </div>
  {_area_bar(turn.area_decision.area)}
  <div class="probability-panel">
    <div class="probability-head">
      <h4>区域概率</h4>
      <span class="probability-caption">点击上面的区域查看当前手牌下的有效分布</span>
    </div>
    <div class="probability-table"></div>
  </div>
  <script type="application/json" class="prob-data">{probability_data}</script>
  <div class="turn-grid">
    <div class="turn-detail">
      <span>摸到</span>
      <b>{_tile_label(turn.drawn_tile)}</b>
    </div>
    <div class="turn-detail">
      <span>处理</span>
      <b>{escape(discard_text)}</b>
    </div>
    <div class="turn-detail">
      <span>保护</span>
      <b>{escape(protection)}</b>
    </div>
    <div class="turn-detail">
      <span>重抽</span>
      <b>{turn.replays}</b>
    </div>
  </div>
  <dl class="reason-list">
    <div><dt>目标牌</dt><dd>{escape(targets)}</dd></div>
    <div><dt>目标权重</dt><dd>{turn.area_decision.target_weight} / {EXPECTED_WEIGHT_SUM}</dd></div>
    <div><dt>舍牌理由</dt><dd>{escape(discard.reason)}</dd></div>
    <div><dt>舍后受入</dt><dd>{escape(accepts)}</dd></div>
  </dl>
  {_turn_hand_flow(turn)}
</article>
"""


def _area_bar(active_area: int) -> str:
    buttons = []
    for area in range(1, AREA_COUNT + 1):
        selected = " selected" if area == active_area else ""
        buttons.append(
            f'<button class="area{selected}" type="button" data-area="{area}" '
            f'onclick="showArea(this)"><span>区域 {area}</span></button>'
        )
    return f'<div class="area-bar">{"".join(buttons)}</div>'


def _compact_hand(title: str, tiles: tuple[int, ...]) -> str:
    return f"""
<div class="compact-hand">
  <span>{escape(title)}</span>
  <div class="tiles small">{''.join(_tile_html(tile_id) for tile_id in tiles)}</div>
</div>
"""


def _turn_hand_flow(turn: ReplayTurn) -> str:
    discard_tile = turn.discard_decision.discard_tile
    hand_discard = None if discard_tile == turn.drawn_tile else discard_tile
    drawn_discarded = discard_tile == turn.drawn_tile
    drawn_classes = "drawn-tile"
    if drawn_discarded:
        drawn_classes += " discarded"
    return f"""
<div class="hand-flow">
  <div class="flow-hand">
    <span>手牌 13张</span>
    <div class="tiles small">{_tiles_html(turn.hand_before, discard_tile=hand_discard)}</div>
  </div>
  <div class="flow-draw">
    <span>摸到第14张</span>
    <div class="{drawn_classes}">{_tile_html(turn.drawn_tile, discarded=drawn_discarded)}</div>
  </div>
</div>
"""


def _tiles_html(tiles: tuple[int, ...], *, discard_tile: int | None = None) -> str:
    used_discard = False
    rendered = []
    for tile_id in tiles:
        discarded = discard_tile is not None and not used_discard and tile_id == discard_tile
        if discarded:
            used_discard = True
        rendered.append(_tile_html(tile_id, discarded=discarded))
    return "".join(rendered)


def _area_probability_data(
    hand_tiles: tuple[int, ...],
    table: NyukyuTable,
) -> dict[str, list[dict[str, object]]]:
    counts = tile_set(hand_tiles).counts
    data: dict[str, list[dict[str, object]]] = {}
    for area in range(1, AREA_COUNT + 1):
        weights = table.weights_for_area(area)
        valid_total = sum(
            weight
            for tile_id, weight in enumerate(weights)
            if weight > 0 and counts[tile_id] < 4
        )
        rows = []
        for tile_id, weight in enumerate(weights):
            if weight <= 0:
                continue
            blocked = counts[tile_id] >= 4
            effective = 0.0 if blocked or valid_total <= 0 else weight / valid_total
            rows.append(
                {
                    "tile": _tile_label(tile_id),
                    "className": _tile_class(tile_id),
                    "count": counts[tile_id],
                    "weight": weight,
                    "raw": weight / EXPECTED_WEIGHT_SUM,
                    "effective": effective,
                    "blocked": blocked,
                    "protect": counts[tile_id] == 3,
                }
            )
        data[str(area)] = rows
    return data


def _json_script(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _strategy_functions(strategy: str) -> tuple[ChooseArea, ChooseDiscard]:
    if strategy == "greedy":
        return choose_greedy_area, choose_greedy_discard
    if strategy == "public":
        return choose_public_area, choose_public_discard
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


def _result_text(replay: ReplayHand) -> str:
    return "和牌" if replay.win else "流局/未和"


def _score_text(score: JanqScore | None) -> str:
    if score is None:
        return "未和"
    yaku = ", ".join(score.yaku) if score.yaku else "无役名"
    if score.yakuman_count:
        return f"役满 x{score.yakuman_count} · {yaku}"
    return f"{score.han}番 · {yaku}"


def _tile_html(tile_id: int | None, *, discarded: bool = False) -> str:
    if tile_id is None:
        return '<span class="tile empty">-</span>'
    discarded_class = " discarded" if discarded else ""
    return (
        f'<span class="tile tile-art tile-id-{tile_id} {_tile_class(tile_id)}{discarded_class}" '
        f'title="{escape(tile_name(tile_id))}">'
        f"{escape(_tile_label(tile_id))}</span>"
    )


def _mini_tile_html(tile_id: int) -> str:
    return (
        f'<span class="mini-tile tile-art tile-id-{tile_id} {_tile_class(tile_id)}">'
        f"{escape(_tile_label(tile_id))}</span>"
    )


def _tile_class(tile_id: int) -> str:
    if 0 <= tile_id <= 8:
        return "man"
    if 9 <= tile_id <= 17:
        return "sou"
    if 18 <= tile_id <= 26:
        return "pin"
    return "honor"


def _tile_label(tile_id: int | None) -> str:
    if tile_id is None:
        return "-"
    name = TILE_NAMES[tile_id]
    honors = {
        "E": "东",
        "S": "南",
        "W": "西",
        "N": "北",
        "P": "白",
        "F": "发",
        "C": "中",
    }
    if name in honors:
        return honors[name]
    suit = name[-1]
    number = name[:-1]
    suit_labels = {"m": "万", "s": "索", "p": "饼"}
    return f"{number}{suit_labels[suit]}"


def _default_output(seed: int, strategy: str, examples: int) -> Path:
    suffix = f"{strategy}_{seed}" if examples == 1 else f"{strategy}_{seed}_{examples}"
    return Path("_runtime") / "replays" / f"janq_replay_{suffix}.html"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate JanQ HTML replay dashboard.")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--strategy", choices=("route_ev", "public", "greedy"), default="route_ev")
    parser.add_argument("--examples", type=int, default=100, help="Number of starting hands to show")
    parser.add_argument("--balls", type=int, default=8)
    parser.add_argument("--max-turns", type=int, default=100)
    parser.add_argument(
        "--source",
        choices=("random", "observed", "auto"),
        default="random",
        help="Use random starts, observed probe starts, or auto fallback.",
    )
    parser.add_argument(
        "--events-path",
        default=str(Path("_runtime") / "logs" / "janq_events.jsonl"),
        help="Probe JSONL path used by --source observed/auto.",
    )
    parser.add_argument(
        "--resource-dir",
        help="Directory containing copied JanQ resources, for example allresourse.",
    )
    parser.add_argument("--output", help="Output HTML path")
    args = parser.parse_args(argv)

    replay_set = simulate_replay_set(
        seed=args.seed,
        strategy=args.strategy,
        examples=args.examples,
        balls=args.balls,
        max_turns=args.max_turns,
        source=args.source,
        events_path=args.events_path,
    )
    output = Path(args.output) if args.output else _default_output(args.seed, args.strategy, args.examples)
    path = write_replay_set_html(replay_set, output, resource_dir=args.resource_dir)
    print(path)


_CSS = r"""
:root {
  color-scheme: light;
  --bg: #f5f7f8;
  --paper: #ffffff;
  --ink: #16201f;
  --muted: #66706f;
  --line: #d9e0de;
  --teal: #167c72;
  --teal-dark: #0d5f57;
  --amber: #d68b1f;
  --red: #c54949;
  --green: #2f8f5b;
  --blue: #2f6fa8;
  --shadow: 0 14px 40px rgba(20, 33, 31, 0.09);
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg);
  color: var(--ink);
  font-family: "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
  letter-spacing: 0;
}

button {
  font: inherit;
}

.shell {
  width: min(1480px, calc(100vw - 28px));
  margin: 0 auto;
  padding: 24px 0 34px;
}

.hero,
.panel,
.example-panel,
.replay-card,
.turn,
.hand-panel {
  background: var(--paper);
  border: 1px solid var(--line);
  box-shadow: var(--shadow);
}

.hero {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  align-items: center;
  padding: 24px;
  border-radius: 8px;
}

.eyebrow {
  margin: 0 0 6px;
  color: var(--teal-dark);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: .08em;
  text-transform: uppercase;
}

h1,
h2,
h3,
h4,
p {
  margin-top: 0;
}

h1 {
  margin-bottom: 8px;
  font-size: clamp(28px, 4vw, 44px);
  line-height: 1.05;
}

.subtle,
.source {
  margin-bottom: 0;
  color: var(--muted);
}

.source {
  margin-top: 6px;
  font-size: 13px;
}

.stat-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(84px, 1fr));
  gap: 8px;
  min-width: 300px;
}

.stat-strip span {
  padding: 12px;
  border-radius: 8px;
  background: #eef8f5;
  color: var(--teal-dark);
  text-align: center;
}

.stat-strip b {
  display: block;
  font-size: 24px;
}

.panel {
  margin-top: 14px;
  padding: 18px 20px;
  border-radius: 8px;
}

.strategy-panel p {
  margin-bottom: 0;
  color: #3e4a48;
  line-height: 1.7;
}

.workspace {
  display: grid;
  grid-template-columns: minmax(260px, 320px) minmax(0, 1fr);
  gap: 16px;
  margin-top: 16px;
  align-items: start;
}

.example-panel {
  position: sticky;
  top: 12px;
  max-height: calc(100vh - 24px);
  overflow: hidden;
  border-radius: 8px;
}

.example-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 14px 10px;
  border-bottom: 1px solid var(--line);
}

.example-panel-head h2 {
  margin: 0;
  font-size: 18px;
}

.example-panel-head span {
  color: var(--muted);
  font-size: 13px;
}

.example-list {
  display: grid;
  gap: 8px;
  max-height: calc(100vh - 90px);
  overflow: auto;
  padding: 12px;
}

.example-button {
  width: 100%;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfc;
  color: var(--ink);
  cursor: pointer;
  text-align: left;
}

.example-button:hover,
.example-button.active {
  border-color: var(--teal);
  background: #eef8f5;
}

.example-top,
.example-meta,
.example-hand {
  display: flex;
  align-items: center;
}

.example-top {
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 7px;
}

.example-meta {
  margin-top: 8px;
  color: var(--muted);
  font-size: 12px;
}

.example-hand {
  flex-wrap: wrap;
  gap: 2px;
}

.result-pill {
  padding: 3px 7px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}

.result-pill.win {
  background: #e9f6ee;
  color: var(--green);
}

.result-pill.lose {
  background: #f4f0e8;
  color: #8b681f;
}

.replay-stage {
  min-width: 0;
}

.replay-card {
  display: none;
  border-radius: 8px;
  padding: 16px;
}

.replay-card.active {
  display: block;
}

.replay-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
}

.replay-head h2 {
  margin-bottom: 6px;
  font-size: 24px;
}

.score-box {
  min-width: 160px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #fff6e8;
  color: #805813;
  font-weight: 700;
  text-align: center;
}

.hand-panel {
  margin-top: 14px;
  padding: 14px;
  border-radius: 8px;
  box-shadow: none;
}

.hand-panel h3 {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
  font-size: 16px;
}

.aside {
  color: var(--muted);
  font-size: 13px;
  font-weight: 500;
}

.tiles {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.tile {
  display: inline-grid;
  place-items: center;
  width: 38px;
  height: 52px;
  border: 1px solid #cfd7d5;
  border-bottom-width: 3px;
  border-radius: 6px;
  background: #fffdf7;
  color: var(--ink);
  font-weight: 800;
  font-size: 15px;
  line-height: 1;
}

.tiles.small {
  gap: 4px;
}

.tiles.small .tile {
  width: 28px;
  height: 38px;
  font-size: 12px;
  border-radius: 5px;
}

.mini-tile {
  display: inline-grid;
  place-items: center;
  width: 17px;
  height: 23px;
  border-radius: 4px;
  background: #fffdf7;
  border: 1px solid #d5dcda;
  font-size: 9px;
  font-weight: 800;
}

.man {
  color: var(--red);
}

.sou {
  color: var(--green);
}

.pin {
  color: var(--blue);
}

.honor {
  color: #1a1f1e;
}

.tile.discarded {
  border-color: var(--red);
  background: #fff1f1;
  box-shadow: 0 0 0 2px rgba(197, 73, 73, 0.18);
}

.turn-list {
  display: grid;
  gap: 14px;
  margin-top: 14px;
}

.turn {
  border-radius: 8px;
  padding: 14px;
  box-shadow: none;
}

.turn-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}

.turn-head h3 {
  margin-bottom: 5px;
  font-size: 18px;
}

.turn-head p {
  margin-bottom: 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.5;
}

.balls {
  flex: 0 0 auto;
  padding: 8px 10px;
  border-radius: 8px;
  background: #eef8f5;
  color: var(--teal-dark);
  font-weight: 700;
}

.area-bar {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 7px;
  margin: 14px 0 12px;
}

.area {
  min-height: 44px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfc;
  color: var(--ink);
  cursor: pointer;
  font-weight: 700;
}

.area:hover,
.area.selected {
  border-color: var(--teal);
  background: #e7f5f2;
  color: var(--teal-dark);
}

.probability-panel {
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
  background: #fcfdfd;
}

.probability-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
  padding: 9px 11px;
  border-bottom: 1px solid var(--line);
}

.probability-head h4 {
  margin: 0;
  font-size: 14px;
}

.probability-caption {
  color: var(--muted);
  font-size: 12px;
}

.probability-table {
  max-height: 210px;
  overflow: auto;
  padding: 10px;
}

.prob-chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.prob-chip {
  display: inline-flex;
  gap: 5px;
  align-items: baseline;
  min-height: 26px;
  padding: 4px 7px;
  border: 1px solid #dfe6e4;
  border-radius: 6px;
  background: #ffffff;
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
}

.prob-chip b {
  color: var(--ink);
  font-weight: 800;
}

.prob-separator {
  color: var(--muted);
}

.prob-chip.protect {
  border-color: #eccd88;
  background: #fff8e8;
}

.prob-empty {
  color: var(--muted);
  font-size: 12px;
}

.turn-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-top: 12px;
}

.turn-detail {
  padding: 10px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: #fbfcfc;
}

.turn-detail span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 3px;
}

.turn-detail b {
  font-size: 16px;
}

.reason-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 14px;
  margin: 12px 0 0;
}

.reason-list div {
  min-width: 0;
}

.reason-list dt {
  color: var(--muted);
  font-size: 12px;
}

.reason-list dd {
  margin: 2px 0 0;
  overflow-wrap: anywhere;
  font-size: 13px;
}

.hand-columns {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.compact-hand {
  min-width: 0;
  padding: 10px;
  border-radius: 8px;
  background: #f7f9f9;
  border: 1px solid var(--line);
}

.compact-hand > span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 7px;
}

.hand-flow {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 84px;
  gap: 10px;
  margin-top: 12px;
}

.flow-hand,
.flow-draw {
  min-width: 0;
  padding: 10px;
  border-radius: 8px;
  background: #f7f9f9;
  border: 1px solid var(--line);
}

.flow-hand > span,
.flow-draw > span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 7px;
}

.flow-draw {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.drawn-tile.discarded {
  padding: 3px;
  border-radius: 8px;
  background: #fff1f1;
}

.empty-state {
  margin-top: 14px;
  padding: 18px;
  border: 1px dashed var(--line);
  border-radius: 8px;
  color: var(--muted);
  text-align: center;
}

@media (max-width: 980px) {
  .hero,
  .replay-head,
  .turn-head {
    display: block;
  }

  .stat-strip {
    margin-top: 16px;
    min-width: 0;
  }

  .workspace {
    grid-template-columns: 1fr;
  }

  .example-panel {
    position: static;
    max-height: none;
  }

  .example-list {
    max-height: 340px;
  }

  .area-bar,
  .turn-grid,
  .reason-list,
  .hand-columns,
  .hand-flow {
    grid-template-columns: 1fr;
  }
}
"""


_JS = r"""
function showReplay(index) {
  document.querySelectorAll('.example-button').forEach((button) => {
    button.classList.toggle('active', Number(button.dataset.replayIndex) === index);
  });
  document.querySelectorAll('.replay-card').forEach((card) => {
    card.classList.toggle('active', Number(card.dataset.replayIndex) === index);
  });
}

function showArea(button) {
  const turn = button.closest('.turn');
  if (!turn) {
    return;
  }
  const area = String(button.dataset.area);
  turn.querySelectorAll('.area').forEach((item) => item.classList.remove('selected'));
  button.classList.add('selected');

  const dataNode = turn.querySelector('.prob-data');
  const target = turn.querySelector('.probability-table');
  if (!dataNode || !target) {
    return;
  }
  const data = JSON.parse(dataNode.textContent);
  const rows = data[area] || [];
  target.innerHTML = renderProbabilityTable(area, rows);
}

function renderProbabilityTable(area, rows) {
  const visible = rows.filter((row) => Number(row.effective) > 0);
  if (!visible.length) {
    return '<div class="prob-empty">无有效牌</div>';
  }
  const body = visible.map((row) => {
    const classes = ['prob-chip', row.protect ? 'protect' : ''].filter(Boolean).join(' ');
    const tileClass = escapeHtml(row.className || '');
    return `<span class="${classes}"><span class="${tileClass}">${escapeHtml(row.tile)}</span><span class="prob-separator">-</span><b>${formatPercent(row.effective)}</b></span>`;
  }).join('');
  return `<div class="prob-chip-list" aria-label="区域 ${area} 概率">${body}</div>`;
}

function formatPercent(value) {
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

document.querySelectorAll('.turn').forEach((turn) => {
  const button = turn.querySelector('.area.selected') || turn.querySelector('.area');
  if (button) {
    showArea(button);
  }
});
showReplay(0);
"""


if __name__ == "__main__":
    main()
