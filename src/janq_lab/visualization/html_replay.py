"""Generate self-contained HTML replay dashboards for JanQ simulations."""

from __future__ import annotations

from dataclasses import dataclass, replace
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
from janq_lab.assets.special import SpecialHandRecord, load_special_tables
from janq_lab.analysis.economy_monte_carlo import (
    choose_enabled_record,
    choose_paren_number,
)
from janq_lab.model.economy import payout_for_score, yakuman_challenge_payout
from janq_lab.model.haipai import load_observed_normal_haipai, random_wall_hand
from janq_lab.model.hand import (
    TileSet,
    is_complete_hand,
    shanten,
    tile_set,
    winning_tiles,
)
from janq_lab.model.scoring import JanqScore, score_hand
from janq_lab.strategy.greedy import (
    AreaDecision,
    DiscardDecision,
    choose_greedy_area,
    choose_greedy_discard,
)
from janq_lab.strategy.bonus import choose_bonus_area, choose_bonus_discard
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
    riichi_before: bool = False
    riichi_declared: bool = False
    ippatsu_chance: bool = False

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
    ura_dora_id: int | None = None
    riichi: bool = False
    riichi_turn: int | None = None
    double_riichi: bool = False
    ippatsu_win: bool = False
    source_label: str = "随机起手"
    mode: str = "normal"
    mode_index: int = 0
    payout: int = 0
    cumulative_payout: int = 0
    cumulative_yakuman_units: int = 0
    hold_hand: bool = False
    bonus_hands: tuple["ReplayHand", ...] = ()

    @property
    def bonus_payout(self) -> int:
        return sum(hand.payout for hand in self.bonus_hands)

    @property
    def total_payout(self) -> int:
        return self.payout + self.bonus_payout


@dataclass(frozen=True)
class ReplaySet:
    seed: int
    strategy: str
    replays: tuple[ReplayHand, ...]
    source_label: str
    table_name: str
    observed_hand_count: int = 0
    bet: int = 10
    paren_table_mode: str = "previous_han"
    include_bonus: bool = True


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
    (885, 342, 1000, 510),
    (20, 512, 135, 680),
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
    bet: int = 10,
    include_bonus: bool = True,
    paren_table_mode: str = "previous_han",
    max_bonus_hands: int = 100,
    mode: str = "normal",
    mode_index: int = 0,
    _rng: random.Random | None = None,
    _dora_id: int | None = None,
    _ura_dora_id: int | None = None,
    _randomize_dora: bool = True,
    hold_hand: bool = False,
) -> ReplayHand:
    if balls < 1:
        raise ValueError("balls must be positive")
    if max_turns < 1:
        raise ValueError("max_turns must be positive")
    if bet < 1:
        raise ValueError("bet must be positive")
    if max_bonus_hands < 1:
        raise ValueError("max_bonus_hands must be positive")

    rng = _rng if _rng is not None else random.Random(seed)
    table = table or load_tables()["nyukyu_base_table.bytes"]
    choose_area, choose_discard = _strategy_functions(strategy)
    hand = tile_set(initial_hand) if initial_hand is not None else random_wall_hand(rng)
    if hand.size != 13:
        raise ValueError(f"initial hand must have 13 tiles, got {hand.size}")
    original = hand.to_tiles()
    dora_id = rng.randrange(34) if _randomize_dora else _dora_id
    ura_dora_id = rng.randrange(34) if _randomize_dora else _ura_dora_id

    turns: list[ReplayTurn] = []
    current_balls = balls
    riichi_active = False
    riichi_turn: int | None = None
    shots_after_riichi = 0
    for turn_number in range(1, max_turns + 1):
        if current_balls <= 0:
            break
        hand_before = hand.to_tiles()
        area_decision = _call_choose_area(
            choose_area,
            hand,
            table,
            current_balls,
            dora_id=dora_id,
            ura_dora_id=ura_dora_id,
            is_reach=riichi_active,
        )
        balls_before = current_balls
        current_balls -= 1
        ippatsu_chance = riichi_active and shots_after_riichi == 0

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
        if hold_hand:
            if is_complete_hand(hand):
                discard_decision = DiscardDecision(
                    True,
                    None,
                    None,
                    (),
                    "bonus_hold_agari",
                )
            else:
                locked_hand = hand.with_removed_one(drawn_tile)
                discard_decision = DiscardDecision(
                    False,
                    drawn_tile,
                    shanten(locked_hand),
                    winning_tiles(locked_hand),
                    "bonus_hold_auto_discard",
                )
        else:
            discard_decision = _call_choose_discard(
                choose_discard,
                hand,
                current_balls,
                dora_id=dora_id,
                ura_dora_id=ura_dora_id,
                is_reach=riichi_active,
                turn=turn_number,
                drawn_tile=drawn_tile,
            )
        riichi_declared = (not riichi_active) and discard_decision.declare_riichi

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
                    riichi_before=riichi_active,
                    riichi_declared=riichi_declared,
                    ippatsu_chance=ippatsu_chance,
                )
            )
            score = score_hand(
                hand,
                dora_id=dora_id,
                ura_dora_id=ura_dora_id,
                reach=riichi_active and riichi_turn != 1,
                double_reach=riichi_turn == 1,
                ippatsu=ippatsu_chance,
            )
            replay = ReplayHand(
                seed=seed,
                strategy=strategy,
                initial_hand=original,
                turns=tuple(turns),
                final_hand=hand.to_tiles(),
                win=True,
                score=score,
                dora_id=dora_id,
                ura_dora_id=ura_dora_id,
                riichi=riichi_active,
                riichi_turn=riichi_turn,
                double_riichi=riichi_turn == 1,
                ippatsu_win=ippatsu_chance,
                source_label=source_label,
                mode=mode,
                mode_index=mode_index,
                payout=payout_for_score(score, bet=bet),
                cumulative_payout=payout_for_score(score, bet=bet),
                hold_hand=hold_hand,
            )
            if include_bonus and mode == "normal":
                return _attach_bonus_hands(
                    replay,
                    rng=rng,
                    bet=bet,
                    paren_table_mode=paren_table_mode,
                    max_bonus_hands=max_bonus_hands,
                    max_turns=max_turns,
                )
            return replay

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
                riichi_before=riichi_active,
                riichi_declared=riichi_declared,
                ippatsu_chance=ippatsu_chance,
            )
        )
        if riichi_declared:
            riichi_active = True
            riichi_turn = turn_number
            shots_after_riichi = 0
        elif riichi_active:
            shots_after_riichi += 1

    return ReplayHand(
        seed=seed,
        strategy=strategy,
        initial_hand=original,
        turns=tuple(turns),
        final_hand=hand.to_tiles(),
        win=False,
        score=None,
        dora_id=dora_id,
        ura_dora_id=ura_dora_id,
        riichi=riichi_active,
        riichi_turn=riichi_turn,
        double_riichi=riichi_turn == 1,
        ippatsu_win=False,
        source_label=source_label,
        mode=mode,
        mode_index=mode_index,
        hold_hand=hold_hand,
    )


def _attach_bonus_hands(
    normal: ReplayHand,
    *,
    rng: random.Random,
    bet: int,
    paren_table_mode: str,
    max_bonus_hands: int,
    max_turns: int,
) -> ReplayHand:
    if normal.score is None:
        return normal

    tables = load_tables()
    special = load_special_tables()
    bonus_hands: list[ReplayHand] = []
    cumulative_payout = normal.payout
    enter_yakuman = normal.score.is_yakuman
    initial_yakuman_units = max(1, normal.score.yakuman_count) if normal.score.is_yakuman else 0
    previous_score = normal.score

    if not enter_yakuman:
        agari_count = 1
        for bonus_index in range(1, max_bonus_hands + 1):
            if agari_count >= 8:
                enter_yakuman = True
                break
            number = choose_paren_number(
                previous_score,
                special,
                rng,
                mode=paren_table_mode,
            )
            records = special.paren_tables[number].records
            record = choose_enabled_record(records, rng)
            replay = simulate_replay(
                seed=normal.seed,
                strategy="bonus",
                balls=3,
                initial_hand=record.tiles,
                max_turns=max_turns,
                source_label=(
                    f"普通奖励 #{bonus_index} · paren_{number} "
                    f"· record {_record_number(records, record)}"
                ),
                table=tables["nyukyu_paren_table.bytes"],
                bet=bet,
                include_bonus=False,
                paren_table_mode=paren_table_mode,
                max_bonus_hands=max_bonus_hands,
                mode="paren",
                mode_index=bonus_index,
                _rng=rng,
                _dora_id=record.dora_id,
                _ura_dora_id=None,
                _randomize_dora=False,
                hold_hand=True,
            )
            cumulative_payout += replay.payout
            replay = replace(replay, cumulative_payout=cumulative_payout)
            bonus_hands.append(replay)
            if not replay.win or replay.score is None:
                break
            agari_count += 1
            previous_score = replay.score
            if replay.score.is_yakuman or agari_count >= 8:
                if replay.score.is_yakuman:
                    initial_yakuman_units += max(1, replay.score.yakuman_count)
                enter_yakuman = True
                break

    if enter_yakuman:
        cumulative_yakuman_units = initial_yakuman_units
        records = special.yakuman_records
        for bonus_index in range(1, max_bonus_hands + 1):
            record = choose_enabled_record(records, rng)
            replay = simulate_replay(
                seed=normal.seed,
                strategy="bonus",
                balls=3,
                initial_hand=record.tiles,
                max_turns=max_turns,
                source_label=(
                    f"役满奖励 #{bonus_index} · yakuman_table "
                    f"· record {_record_number(records, record)}"
                ),
                table=tables["nyukyu_yakuman_table.bytes"],
                bet=bet,
                include_bonus=False,
                paren_table_mode=paren_table_mode,
                max_bonus_hands=max_bonus_hands,
                mode="yakuman",
                mode_index=bonus_index,
                _rng=rng,
                _dora_id=None,
                _ura_dora_id=None,
                _randomize_dora=False,
                hold_hand=True,
            )
            if replay.win and replay.score is not None:
                cumulative_yakuman_units += max(1, replay.score.yakuman_count)
                payout = yakuman_challenge_payout(
                    bet=bet,
                    cumulative_yakuman_count=cumulative_yakuman_units,
                )
                cumulative_payout += payout
                replay = replace(
                    replay,
                    payout=payout,
                    cumulative_payout=cumulative_payout,
                    cumulative_yakuman_units=cumulative_yakuman_units,
                )
            else:
                replay = replace(
                    replay,
                    payout=0,
                    cumulative_payout=cumulative_payout,
                    cumulative_yakuman_units=cumulative_yakuman_units,
                )
            bonus_hands.append(replay)
            if not replay.win:
                break

    return replace(normal, bonus_hands=tuple(bonus_hands))


def _record_number(
    records: tuple[SpecialHandRecord, ...],
    selected: SpecialHandRecord,
) -> int:
    return records.index(selected) + 1


def simulate_replay_set(
    *,
    seed: int = 1,
    strategy: str = "route_ev",
    examples: int = 100,
    balls: int = 8,
    max_turns: int = 100,
    source: str = "random",
    events_path: str | Path | None = None,
    bet: int = 10,
    include_bonus: bool = True,
    paren_table_mode: str = "previous_han",
    max_bonus_hands: int = 100,
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
                bet=bet,
                include_bonus=include_bonus,
                paren_table_mode=paren_table_mode,
                max_bonus_hands=max_bonus_hands,
            )
        )

    return ReplaySet(
        seed=seed,
        strategy=strategy,
        replays=tuple(replays),
        source_label=source_label,
        table_name=table.name,
        observed_hand_count=len(observed_hands),
        bet=bet,
        paren_table_mode=paren_table_mode,
        include_bonus=include_bonus,
    )


def render_replay_html(
    replay: ReplayHand,
    *,
    resource_dir: str | Path | None = None,
    output_path: str | Path | None = None,
    review_ui: bool = False,
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
        review_ui=review_ui,
    )


def render_replay_set_html(
    replay_set: ReplaySet,
    *,
    resource_dir: str | Path | None = None,
    output_path: str | Path | None = None,
    review_ui: bool = False,
) -> str:
    tables = load_tables()
    table = tables[replay_set.table_name]
    assets = discover_tile_image_assets(resource_dir=resource_dir, output_path=output_path)
    title = f"JanQ replay dashboard seed {replay_set.seed}"
    css = f"{_CSS}{_asset_css(assets)}{_REVIEW_UI_CSS if review_ui else ''}"
    script = f"{_JS}{_REVIEW_UI_JS if review_ui else ''}"
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{escape(title)}</title>",
            f"<style>{css}</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            _render_header(replay_set, assets),
            _render_economy_summary(replay_set),
            _render_strategy_note(replay_set.strategy),
            _render_review_toolbar() if review_ui else "",
            '<section class="workspace">',
            _render_example_list(replay_set),
            '<div class="replay-stage">',
            "".join(
                _render_replay_card(replay, index, tables, bet=replay_set.bet)
                for index, replay in enumerate(replay_set.replays)
            ),
            "</div>",
            "</section>",
            _render_review_prompt_section() if review_ui else "",
            "</main>",
            f"<script>{script}</script>",
            "</body>",
            "</html>",
        )
    )


def write_replay_html(
    replay: ReplayHand,
    output: str | Path,
    *,
    resource_dir: str | Path | None = None,
    review_ui: bool = False,
) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_replay_html(
            replay,
            resource_dir=resource_dir,
            output_path=path,
            review_ui=review_ui,
        ),
        encoding="utf-8",
    )
    return path


def write_replay_set_html(
    replay_set: ReplaySet,
    output: str | Path,
    *,
    resource_dir: str | Path | None = None,
    review_ui: bool = False,
) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_replay_set_html(
            replay_set,
            resource_dir=resource_dir,
            output_path=path,
            review_ui=review_ui,
        ),
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
    bonus_wins = sum(
        1
        for replay in replay_set.replays
        for bonus in replay.bonus_hands
        if bonus.win
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
    <span><b>{bonus_wins}</b> 奖励和牌</span>
  </div>
</section>
"""


def _render_economy_summary(replay_set: ReplaySet) -> str:
    stats = _economy_stats(replay_set)
    net_class = "positive" if stats["net"] >= 0 else "negative"
    return f"""
<section class="panel economy-panel">
  <div class="economy-head">
    <div>
      <p class="eyebrow">完整游戏经济</p>
      <h2>总返还 {stats["total_payout"]} / 净收益 <span class="{net_class}">{_signed(stats["net"])}</span></h2>
      <p>投入 {stats["total_bet"]} · RTP {_percent(stats["rtp"])} · ROI {_signed_percent(stats["roi"])}</p>
    </div>
    <div class="economy-counts">
      <span>普通和牌 <b>{stats["normal_wins"]}</b></span>
      <span>普通奖励 <b>{stats["paren_wins"]}</b></span>
      <span>役满奖励 <b>{stats["yakuman_wins"]}</b></span>
    </div>
  </div>
  <div class="payout-breakdown">
    <div><span>普通非役满</span><b>{stats["normal_non_yakuman_payout"]}</b></div>
    <div><span>普通役满</span><b>{stats["normal_yakuman_payout"]}</b></div>
    <div><span>普通奖励游戏</span><b>{stats["paren_payout"]}</b></div>
    <div><span>役满奖励游戏</span><b>{stats["yakuman_payout"]}</b></div>
  </div>
</section>
"""


def _economy_stats(replay_set: ReplaySet) -> dict[str, int | float]:
    normal_non_yakuman_payout = sum(
        replay.payout
        for replay in replay_set.replays
        if replay.score is not None and not replay.score.is_yakuman
    )
    normal_yakuman_payout = sum(
        replay.payout
        for replay in replay_set.replays
        if replay.score is not None and replay.score.is_yakuman
    )
    paren_hands = [
        bonus
        for replay in replay_set.replays
        for bonus in replay.bonus_hands
        if bonus.mode == "paren"
    ]
    yakuman_hands = [
        bonus
        for replay in replay_set.replays
        for bonus in replay.bonus_hands
        if bonus.mode == "yakuman"
    ]
    paren_payout = sum(hand.payout for hand in paren_hands)
    yakuman_payout = sum(hand.payout for hand in yakuman_hands)
    total_bet = len(replay_set.replays) * replay_set.bet
    total_payout = (
        normal_non_yakuman_payout
        + normal_yakuman_payout
        + paren_payout
        + yakuman_payout
    )
    net = total_payout - total_bet
    return {
        "total_bet": total_bet,
        "total_payout": total_payout,
        "net": net,
        "rtp": total_payout / total_bet if total_bet else 0.0,
        "roi": net / total_bet if total_bet else 0.0,
        "normal_wins": sum(1 for replay in replay_set.replays if replay.win),
        "paren_wins": sum(1 for hand in paren_hands if hand.win),
        "yakuman_wins": sum(1 for hand in yakuman_hands if hand.win),
        "normal_non_yakuman_payout": normal_non_yakuman_payout,
        "normal_yakuman_payout": normal_yakuman_payout,
        "paren_payout": paren_payout,
        "yakuman_payout": yakuman_payout,
    }


def _render_strategy_note(strategy: str) -> str:
    if strategy == "route_ev":
        body = (
            "普通局优先检索役满路线：四暗刻、大三元、九莲、国士；"
            "役满路线激活后，区域选择偏向路线前进、直接和牌和第四张保护。"
            "四暗刻/大三元舍牌会额外保留未来最可能继续打的花色和混一色后路。"
            "没有明确役满路线时回到 public 路线。奖励局使用三球听牌专用区域策略，"
            "但手牌 HOLD 锁定，未和牌时只能自动摸切。"
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


def _render_review_toolbar() -> str:
    return """
<section class="panel review-toolbar">
  <div class="review-progress">
    <div class="review-progress-line">
      <b id="reviewProgressText">已审核 0 / 0</b>
      <span id="reviewIssueCount">不赞同 0 · 有疑问 0</span>
    </div>
    <div class="progress-track"><span id="reviewProgressFill"></span></div>
  </div>
  <div class="review-toolbar-actions">
    <label>
      决策
      <select id="actionFilter" aria-label="决策筛选">
        <option value="all">全部</option>
        <option value="shot">发射</option>
        <option value="discard">弃牌/立直</option>
      </select>
    </label>
    <label>
      审核
      <select id="reviewFilter" aria-label="审核筛选">
        <option value="all">全部</option>
        <option value="pending">未审核</option>
        <option value="agree">赞同</option>
        <option value="question">有疑问</option>
        <option value="disagree">不赞同</option>
      </select>
    </label>
    <button type="button" class="command-button" id="nextUnreviewed">下一个未审核</button>
    <button type="button" class="command-button primary" id="generatePrompt">生成修正文本</button>
  </div>
</section>
"""


def _render_review_prompt_section() -> str:
    return """
<section class="prompt-section" id="promptSection">
  <div class="prompt-head">
    <div>
      <p class="eyebrow">Strategy Revision</p>
      <h2>策略修正文本</h2>
      <p class="subtle" id="promptSummary">尚未生成</p>
    </div>
    <button type="button" class="command-button" id="copyPrompt">复制</button>
  </div>
  <textarea id="promptOutput" spellcheck="false"></textarea>
</section>
"""


def _render_example_list(replay_set: ReplaySet) -> str:
    buttons = []
    for index, replay in enumerate(replay_set.replays):
        result = _result_text(replay)
        net = replay.total_payout - replay_set.bet
        active = " active" if index == 0 else ""
        buttons.append(
            f"""
<button class="example-button{active}" type="button" data-replay-index="{index}" onclick="showReplay({index})">
  <span class="example-top">
    <b>#{index + 1}</b>
    <span class="result-pill {('win' if replay.win else 'lose')}">{escape(result)}</span>
  </span>
  <span class="example-hand">{''.join(_mini_tile_html(tile_id) for tile_id in replay.initial_hand)}</span>
  <span class="example-meta">返还 {replay.total_payout} · 净 {_signed(net)} · 奖励局 {len(replay.bonus_hands)}</span>
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


def _render_replay_card(
    replay: ReplayHand,
    index: int,
    tables: dict[str, NyukyuTable],
    *,
    bet: int,
) -> str:
    active = " active" if index == 0 else ""
    riichi_meta = "无立直" if not replay.riichi else (
        f"{'双立直' if replay.double_riichi else '立直'} 第{replay.riichi_turn}球"
        + (" / 一发" if replay.ippatsu_win else "")
    )
    return f"""
<section class="replay-card{active}" data-replay-index="{index}">
  <div class="replay-head">
    <div>
      <p class="eyebrow">Example #{index + 1}</p>
      <h2>{escape(_result_text(replay))}</h2>
      <p class="subtle">seed={replay.seed} · {escape(replay.source_label)} · Dora: {_tile_label(replay.dora_id)} · Ura: {_tile_label(replay.ura_dora_id)} · {escape(riichi_meta)}</p>
    </div>
    <div class="score-box">{escape(_score_text(replay.score))}</div>
  </div>
  {_render_hand_panel("起手", replay.initial_hand)}
  {_render_turns(replay, tables["nyukyu_base_table.bytes"])}
  {_render_hand_panel("回合后", replay.final_hand, aside=_score_text(replay.score))}
  {_render_session_economy(replay, bet=bet)}
  {_render_bonus_chain(replay, tables)}
</section>
"""


def _render_session_economy(replay: ReplayHand, *, bet: int) -> str:
    paren_hands = tuple(hand for hand in replay.bonus_hands if hand.mode == "paren")
    yakuman_hands = tuple(hand for hand in replay.bonus_hands if hand.mode == "yakuman")
    paren_payout = sum(hand.payout for hand in paren_hands)
    yakuman_payout = sum(hand.payout for hand in yakuman_hands)
    net = replay.total_payout - bet
    net_class = "positive" if net >= 0 else "negative"
    return f"""
<section class="session-economy">
  <div class="session-economy-head">
    <div>
      <p class="eyebrow">本局完整收益</p>
      <h3>返还 {replay.total_payout} · <span class="{net_class}">净 {_signed(net)}</span></h3>
    </div>
    <span class="session-bet">投入 {bet}</span>
  </div>
  <div class="session-payout-grid">
    <div><span>普通游戏</span><b>{replay.payout}</b></div>
    <div><span>普通奖励</span><b>{paren_payout}</b><small>{sum(hand.win for hand in paren_hands)} 胜</small></div>
    <div><span>役满奖励</span><b>{yakuman_payout}</b><small>{sum(hand.win for hand in yakuman_hands)} 胜</small></div>
    <div><span>奖励总局数</span><b>{len(replay.bonus_hands)}</b><small>含结束失败局</small></div>
  </div>
</section>
"""


def _render_bonus_chain(
    replay: ReplayHand,
    tables: dict[str, NyukyuTable],
) -> str:
    if not replay.bonus_hands:
        if replay.win:
            return '<section class="bonus-empty">本局未生成奖励游戏记录。</section>'
        return '<section class="bonus-empty">普通游戏未和牌，不进入奖励游戏。</section>'

    return f"""
<section class="bonus-chain">
  <div class="bonus-chain-head">
    <div>
      <p class="eyebrow">奖励游戏进程</p>
      <h2>普通游戏之后继续模拟，直到奖励链结束</h2>
    </div>
    <span>{len(replay.bonus_hands)} 局</span>
  </div>
  {''.join(_render_bonus_hand(hand, tables) for hand in replay.bonus_hands)}
</section>
"""


def _render_bonus_hand(
    replay: ReplayHand,
    tables: dict[str, NyukyuTable],
) -> str:
    is_yakuman = replay.mode == "yakuman"
    mode_label = "役满奖励游戏" if is_yakuman else "普通奖励游戏"
    table = tables[
        "nyukyu_yakuman_table.bytes"
        if is_yakuman
        else "nyukyu_paren_table.bytes"
    ]
    status = "成功和牌" if replay.win else "失败，奖励链结束"
    status_class = "win" if replay.win else "lose"
    hold_badge = '<span class="hold-badge">HOLD 锁定</span>' if replay.hold_hand else ""
    units = (
        f'<span>累计役满单位 <b>{replay.cumulative_yakuman_units}</b></span>'
        if is_yakuman
        else ""
    )
    return f"""
<article class="bonus-hand {escape(replay.mode)}">
  <div class="bonus-hand-head">
    <div>
      <p class="eyebrow">{escape(mode_label)} #{replay.mode_index}</p>
      <h3>{escape(status)} {hold_badge}</h3>
      <p>{escape(replay.source_label)} · Dora: {_tile_label(replay.dora_id)}</p>
    </div>
    <div class="bonus-result">
      <span class="result-pill {status_class}">{escape(_score_text(replay.score))}</span>
      <b>本局 +{replay.payout}</b>
      <small>累计返还 {replay.cumulative_payout}</small>
      {units}
    </div>
  </div>
  {_render_hand_panel("奖励起手", replay.initial_hand)}
  {_render_turns(replay, table)}
  {_render_hand_panel("奖励结果", replay.final_hand, aside=_score_text(replay.score))}
</article>
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
    hold_auto_discard = discard.reason == "bonus_hold_auto_discard"
    if discard.is_agari:
        discard_text = "和牌"
    elif hold_auto_discard:
        discard_text = f"HOLD 自动摸切 {_tile_label(discard.discard_tile)}"
    else:
        discard_text = f"弃 {_tile_label(discard.discard_tile)}"
    if discard.declare_riichi:
        discard_text = f"{discard_text} + 立直"
    protection = "保护 +1球" if turn.fourth_copy else "无保护"
    riichi_text = _turn_riichi_text(turn)
    accepts = ", ".join(_tile_label(tile_id) for tile_id in discard.accepts) or "无"
    targets = ", ".join(_tile_label(tile_id) for tile_id in turn.area_decision.target_tiles) or "无"
    probability_data = _json_script(_area_probability_data(turn.hand_before, table))
    hand_before = _tile_refs(turn.hand_before)
    hand_after_draw = _tile_refs((*turn.hand_before, turn.drawn_tile))
    drawn_tile = _tile_ref(turn.drawn_tile)
    discarded_tile = "" if discard.is_agari else _tile_ref(discard.discard_tile)

    return f"""
<article class="turn" data-turn-number="{turn.turn}" data-balls-before="{turn.balls_before}"
  data-balls-after="{turn.balls_after_draw}" data-shot-area="{turn.area_decision.area}"
  data-hold-hand="{str(hold_auto_discard or discard.reason == 'bonus_hold_agari').lower()}"
  data-shot-reason="{escape(turn.area_decision.reason)}" data-shot-targets="{escape(targets)}"
  data-shot-target-weight="{turn.area_decision.target_weight}"
  data-discard-choice="{escape(discard_text)}" data-discard-reason="{escape(discard.reason)}"
  data-discard-accepts="{escape(accepts)}" data-riichi="{str(discard.declare_riichi).lower()}"
  data-drawn-tile="{escape(drawn_tile)}" data-discarded-tile="{escape(discarded_tile)}"
  data-hand-before="{escape(hand_before)}" data-hand-after-draw="{escape(hand_after_draw)}">
  <div class="turn-head">
    <div>
      <h3>第 {turn.turn} 球</h3>
      <p>{escape(turn.area_decision.reason)}</p>
    </div>
    <div class="balls">球数 {turn.balls_before} → {turn.balls_after_draw}</div>
  </div>
  {_area_bar(
      turn.area_decision.area,
      turn.area_decision.target_tiles,
      table,
      turn.area_decision.target_factors,
  )}
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
    <div class="turn-detail">
      <span>立直</span>
      <b>{escape(riichi_text)}</b>
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


def _tile_ref(tile_id: int) -> str:
    return f"{_tile_label(tile_id)}(id={tile_id})"


def _tile_refs(tiles: tuple[int, ...]) -> str:
    return " ".join(_tile_ref(tile_id) for tile_id in tiles)


def _area_bar(
    active_area: int,
    targets: tuple[int, ...],
    table: NyukyuTable,
    target_factors: tuple[float, ...] = (),
) -> str:
    factors = target_factors if len(target_factors) == len(targets) else (1.0,) * len(targets)
    buttons = []
    for area in range(1, AREA_COUNT + 1):
        selected = " selected" if area == active_area else ""
        score = round(
            sum(
                table.tile_weight(area, tile_id) * factor
                for tile_id, factor in zip(targets, factors)
            )
        )
        buttons.append(
            f'<button class="area{selected}" type="button" data-area="{area}" '
            f'onclick="showArea(this)">'
            f'<span class="area-label">区域 {area}</span>'
            f'<span class="area-score">{score}</span>'
            f"</button>"
        )
    return f'<div class="area-bar">{"".join(buttons)}</div>'


def _compact_hand(title: str, tiles: tuple[int, ...]) -> str:
    return f"""
<div class="compact-hand">
  <span>{escape(title)}</span>
  <div class="tiles small">{''.join(_tile_html(tile_id) for tile_id in tiles)}</div>
</div>
"""


def _turn_riichi_text(turn: ReplayTurn) -> str:
    if turn.discard_decision.declare_riichi:
        return "本球宣告"
    if turn.ippatsu_chance:
        return "已立直 / 一发机会"
    if turn.riichi_before:
        return "已立直"
    return "未立直"


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
    <div class="hand-row">
      <div class="tiles small hand-tiles">{_tiles_html(turn.hand_before, discard_tile=hand_discard)}</div>
      <div class="{drawn_classes}" title="摸到第14张">{_tile_html(turn.drawn_tile, discarded=drawn_discarded)}</div>
    </div>
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
    if strategy == "bonus":
        return choose_bonus_area, choose_bonus_discard
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
    *,
    dora_id: int | None = None,
    ura_dora_id: int | None = None,
    is_reach: bool = False,
) -> AreaDecision:
    if getattr(choose_area, "uses_full_context", False):
        return choose_area(
            hand,
            table,
            balls,
            dora_id=dora_id,
            ura_dora_id=ura_dora_id,
            is_reach=is_reach,
        )
    if getattr(choose_area, "uses_context", False):
        return choose_area(hand, table, balls)
    return choose_area(hand, table)


def _call_choose_discard(
    choose_discard: ChooseDiscard,
    hand: TileSet,
    balls: int,
    *,
    dora_id: int | None = None,
    ura_dora_id: int | None = None,
    is_reach: bool = False,
    turn: int | None = None,
    drawn_tile: int | None = None,
) -> DiscardDecision:
    if getattr(choose_discard, "uses_full_context", False):
        return choose_discard(
            hand,
            balls,
            dora_id=dora_id,
            ura_dora_id=ura_dora_id,
            is_reach=is_reach,
            turn=turn,
            drawn_tile=drawn_tile,
        )
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


def _signed(value: int) -> str:
    return f"+{value}" if value >= 0 else str(value)


def _percent(value: int | float) -> str:
    return f"{float(value) * 100:.2f}%"


def _signed_percent(value: int | float) -> str:
    percent = float(value) * 100
    return f"+{percent:.2f}%" if percent >= 0 else f"{percent:.2f}%"


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
    parser.add_argument("--bet", type=int, default=10)
    parser.add_argument(
        "--paren-table-mode",
        choices=("previous_han", "select_table"),
        default="previous_han",
    )
    parser.add_argument("--max-bonus-hands", type=int, default=100)
    parser.add_argument(
        "--no-bonus",
        action="store_true",
        help="Render normal games only.",
    )
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
    parser.add_argument(
        "--review-ui",
        action="store_true",
        help="Add approval controls and strategy-revision prompt export.",
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
        bet=args.bet,
        include_bonus=not args.no_bonus,
        paren_table_mode=args.paren_table_mode,
        max_bonus_hands=args.max_bonus_hands,
    )
    output = Path(args.output) if args.output else _default_output(args.seed, args.strategy, args.examples)
    path = write_replay_set_html(
        replay_set,
        output,
        resource_dir=args.resource_dir,
        review_ui=args.review_ui,
    )
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
  grid-template-columns: repeat(4, minmax(84px, 1fr));
  gap: 8px;
  min-width: 410px;
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

.economy-panel {
  border-left: 4px solid var(--teal);
}

.economy-head,
.session-economy-head,
.bonus-chain-head,
.bonus-hand-head {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  align-items: flex-start;
}

.economy-head h2,
.session-economy-head h3,
.bonus-chain-head h2,
.bonus-hand-head h3 {
  margin-bottom: 5px;
}

.economy-head p,
.bonus-hand-head p {
  margin-bottom: 0;
  color: var(--muted);
}

.economy-counts {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.economy-counts span,
.session-bet {
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #f7f9f9;
  color: var(--muted);
  white-space: nowrap;
}

.economy-counts b {
  margin-left: 5px;
  color: var(--ink);
}

.payout-breakdown,
.session-payout-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-top: 14px;
}

.payout-breakdown div,
.session-payout-grid div {
  min-width: 0;
  padding: 11px;
  border-top: 3px solid #a9c8c2;
  background: #f7f9f9;
}

.payout-breakdown span,
.session-payout-grid span,
.session-payout-grid small {
  display: block;
  color: var(--muted);
  font-size: 12px;
}

.payout-breakdown b,
.session-payout-grid b {
  display: block;
  margin-top: 4px;
  font-size: 22px;
  font-variant-numeric: tabular-nums;
}

.positive {
  color: var(--green);
}

.negative {
  color: var(--red);
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

.hold-badge {
  display: inline-flex;
  margin-left: 6px;
  border: 1px solid #8a6a22;
  border-radius: 5px;
  background: #fff4d8;
  color: #785816;
  padding: 3px 7px;
  font-size: 12px;
  vertical-align: middle;
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

.session-economy {
  margin-top: 16px;
  padding: 16px 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.bonus-chain {
  margin-top: 22px;
}

.bonus-chain-head {
  padding-bottom: 12px;
  border-bottom: 2px solid var(--ink);
}

.bonus-chain-head > span {
  color: var(--muted);
  font-weight: 700;
}

.bonus-hand {
  padding: 20px 0 8px;
  border-bottom: 2px solid var(--line);
}

.bonus-hand.paren {
  border-left: 4px solid var(--blue);
  padding-left: 14px;
}

.bonus-hand.yakuman {
  border-left: 4px solid var(--red);
  padding-left: 14px;
}

.bonus-result {
  display: grid;
  justify-items: end;
  gap: 5px;
  min-width: 170px;
}

.bonus-result > b {
  font-size: 22px;
  color: var(--teal-dark);
}

.bonus-result small,
.bonus-result > span:not(.result-pill) {
  color: var(--muted);
  font-size: 12px;
}

.bonus-empty {
  margin-top: 18px;
  padding: 15px 0;
  border-top: 1px solid var(--line);
  color: var(--muted);
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
  position: relative;
  z-index: 2;
  transform: translateY(-68%);
  transition: transform 140ms ease;
}

.turn-list {
  display: grid;
  gap: 14px;
  margin-top: 14px;
  min-width: 0;
}

.turn {
  border-radius: 8px;
  padding: 14px;
  box-shadow: none;
  min-width: 0;
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
  overflow-wrap: anywhere;
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
  min-width: 0;
}

.area {
  min-height: 44px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfc;
  color: var(--ink);
  cursor: pointer;
  font-weight: 700;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  line-height: 1.15;
}

.area:hover,
.area.selected {
  border-color: var(--teal);
  background: #e7f5f2;
  color: var(--teal-dark);
}

.area-label {
  font-size: 13px;
}

.area-score {
  font-size: 12px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

.area.selected .area-score {
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
  grid-template-columns: repeat(5, minmax(0, 1fr));
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
  margin-top: 12px;
}

.flow-hand {
  min-width: 0;
  padding: 10px 10px 14px;
  border-radius: 8px;
  background: #f7f9f9;
  border: 1px solid var(--line);
}

.flow-hand > span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 7px;
}

.hand-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: flex-end;
  padding-top: 28px;
}

.hand-tiles {
  flex: 0 1 auto;
}

.drawn-tile {
  display: inline-flex;
  align-items: flex-end;
  margin-left: 34px;
}

.drawn-tile .tile {
  width: 28px;
  height: 38px;
  font-size: 12px;
  border-radius: 5px;
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
  .turn-head,
  .economy-head,
  .session-economy-head,
  .bonus-chain-head,
  .bonus-hand-head {
    display: block;
  }

  .stat-strip {
    margin-top: 16px;
    min-width: 0;
    grid-template-columns: repeat(2, minmax(0, 1fr));
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
  .hand-flow,
  .payout-breakdown,
  .session-payout-grid {
    grid-template-columns: 1fr;
  }

  .economy-counts,
  .bonus-result {
    margin-top: 12px;
    justify-content: flex-start;
    justify-items: start;
  }
}
"""


_REVIEW_UI_CSS = r"""
.review-toolbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
}

.review-progress {
  flex: 1;
  min-width: 240px;
}

.review-progress-line,
.review-toolbar-actions,
.prompt-head,
.decision-review-head,
.decision-review-actions,
.feedback-fields {
  display: flex;
  gap: 10px;
  align-items: center;
}

.review-progress-line {
  justify-content: space-between;
  margin-bottom: 8px;
  color: var(--muted);
}

.progress-track {
  height: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: #e4ecea;
}

.progress-track span {
  display: block;
  width: 0;
  height: 100%;
  border-radius: inherit;
  background: var(--teal);
  transition: width .18s ease;
}

.review-toolbar-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
}

.review-toolbar label {
  display: grid;
  gap: 4px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}

.review-toolbar select {
  min-width: 112px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #fff;
  color: var(--ink);
  padding: 8px 10px;
  font: inherit;
}

.command-button {
  min-height: 38px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #fff;
  color: var(--ink);
  padding: 8px 12px;
  cursor: pointer;
}

.command-button.primary {
  border-color: var(--teal);
  background: var(--teal);
  color: #fff;
}

.review-stack {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.decision-review {
  border: 1px solid var(--line);
  border-left: 4px solid #cbd7d5;
  border-radius: 8px;
  background: #fbfdfc;
  padding: 12px;
}

.decision-review[data-verdict="agree"] {
  border-left-color: var(--green);
}

.decision-review[data-verdict="question"] {
  border-left-color: var(--amber);
}

.decision-review[data-verdict="disagree"] {
  border-left-color: var(--red);
}

.decision-review-head {
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 10px;
}

.decision-review-head h4 {
  margin: 0 0 4px;
  font-size: 15px;
}

.decision-review-head p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.45;
}

.review-state-pill {
  display: inline-flex;
  min-width: 64px;
  justify-content: center;
  border-radius: 999px;
  background: #edf2f1;
  color: var(--muted);
  padding: 5px 9px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.review-state-pill.agree {
  background: #e5f4ea;
  color: var(--green);
}

.review-state-pill.question {
  background: #fff3dc;
  color: #9a6100;
}

.review-state-pill.disagree {
  background: #fae8e8;
  color: var(--red);
}

.decision-review-actions {
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.verdict-button {
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #fff;
  color: var(--ink);
  padding: 7px 10px;
  cursor: pointer;
}

.verdict-button.active {
  border-color: var(--teal);
  background: #eaf7f4;
  color: var(--teal-dark);
  font-weight: 700;
}

.feedback-fields {
  align-items: stretch;
}

.feedback-fields label {
  flex: 1;
  display: grid;
  gap: 5px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}

.feedback-fields textarea,
#promptOutput {
  width: 100%;
  resize: vertical;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #fff;
  color: var(--ink);
  padding: 9px 10px;
  font: 13px/1.5 "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
}

.feedback-fields textarea {
  min-height: 62px;
}

.prompt-section {
  margin-top: 14px;
  padding: 18px 20px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
  box-shadow: var(--shadow);
}

.prompt-head {
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}

.prompt-head h2 {
  margin-bottom: 4px;
}

#promptOutput {
  min-height: 260px;
  font-family: Consolas, "Microsoft YaHei", monospace;
}

.decision-review.review-hidden,
.turn.review-hidden {
  display: none;
}

@media (max-width: 900px) {
  .review-toolbar,
  .review-toolbar-actions,
  .decision-review-head,
  .feedback-fields,
  .prompt-head {
    align-items: stretch;
    flex-direction: column;
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


_REVIEW_UI_JS = r"""
(function initReviewUi() {
  const toolbar = document.querySelector('.review-toolbar');
  if (!toolbar) {
    return;
  }

  const storageKey = `janq-sim-review-v2:${location.pathname}:${document.title}`;
  let reviews = loadReviews();
  const labels = {
    pending: '未审核',
    agree: '赞同',
    question: '有疑问',
    disagree: '不赞同',
  };

  function loadReviews() {
    try {
      return JSON.parse(localStorage.getItem(storageKey) || '{}');
    } catch (_error) {
      return {};
    }
  }

  function saveReviews() {
    localStorage.setItem(storageKey, JSON.stringify(reviews));
  }

  function reviewFor(id) {
    return reviews[id] || { verdict: 'pending', reason: '', alternative: '' };
  }

  function replayNumber(card) {
    return Number(card?.dataset.replayIndex || 0) + 1;
  }

  function decisionId(turn, action) {
    const card = turn.closest('.replay-card');
    return `sim:r${card?.dataset.replayIndex || 0}:t${turn.dataset.reviewTurnIndex}:${action}`;
  }

  function ensureReviewPanels() {
    document.querySelectorAll('.replay-card').forEach((card) => {
      card.querySelectorAll('.turn').forEach((turn, index) => {
        if (turn.querySelector('.review-stack')) {
          return;
        }
        turn.dataset.reviewTurnIndex = String(index + 1);
        const shotId = decisionId(turn, 'shot');
        const discardId = decisionId(turn, 'discard');
        const stack = document.createElement('section');
        stack.className = 'review-stack';
        const panels = [
          renderDecisionPanel(
            shotId,
            'shot',
            '发射区域决策',
            `区域 ${turn.dataset.shotArea || '-'} · ${turn.dataset.shotReason || ''}`,
            shotDetails(turn, card),
          ),
        ];
        if (turn.dataset.holdHand !== 'true') {
          panels.push(renderDecisionPanel(
            discardId,
            'discard',
            '弃牌/立直决策',
            `${turn.dataset.discardChoice || '-'} · ${turn.dataset.discardReason || ''}`,
            discardDetails(turn, card),
          ));
        }
        stack.innerHTML = panels.join('');
        turn.appendChild(stack);
      });
    });
  }

  function shotDetails(turn, card) {
    return [
      `样本 #${replayNumber(card)} · 第 ${turn.dataset.turnNumber || '?'} 球 · 球数 ${turn.dataset.ballsBefore || '?'} → ${turn.dataset.ballsAfter || '?'}`,
      `目标牌：${turn.dataset.shotTargets || '-'}`,
      `目标权重：${turn.dataset.shotTargetWeight || '-'} / 10000`,
      `摸前手牌：${turn.dataset.handBefore || '-'}`,
      `事件行：模拟生成，无真实日志行`,
    ];
  }

  function discardDetails(turn, card) {
    return [
      `样本 #${replayNumber(card)} · 第 ${turn.dataset.turnNumber || '?'} 球 · 球数 ${turn.dataset.ballsBefore || '?'} → ${turn.dataset.ballsAfter || '?'}`,
      `摸到：${turn.dataset.drawnTile || '-'}`,
      `处理：${turn.dataset.discardChoice || '-'}${turn.dataset.riichi === 'true' ? '；本球宣告立直' : ''}`,
      `舍后受入：${turn.dataset.discardAccepts || '-'}`,
      `摸后手牌：${turn.dataset.handAfterDraw || '-'}`,
      `事件行：模拟生成，无真实日志行`,
    ];
  }

  function renderDecisionPanel(id, action, title, summary, details) {
    const review = reviewFor(id);
    return `<section class="decision-review" data-decision-id="${escapeHtml(id)}" data-action-type="${escapeHtml(action)}" data-verdict="${escapeHtml(review.verdict)}">
      <div class="decision-review-head">
        <div>
          <h4>${escapeHtml(title)}</h4>
          <p>${escapeHtml(summary)}</p>
        </div>
        <span class="review-state-pill ${escapeHtml(review.verdict)}">${escapeHtml(labels[review.verdict] || labels.pending)}</span>
      </div>
      <div class="decision-review-actions">
        ${verdictButton('pending', review.verdict)}
        ${verdictButton('agree', review.verdict)}
        ${verdictButton('question', review.verdict)}
        ${verdictButton('disagree', review.verdict)}
      </div>
      <div class="feedback-fields">
        <label>理由
          <textarea data-field="reason" placeholder="说明为什么不同意，或具体不确定什么。">${escapeHtml(review.reason)}</textarea>
        </label>
        <label>建议
          <textarea data-field="alternative" placeholder="可选：写出更好的区域、弃牌或立直选择。">${escapeHtml(review.alternative)}</textarea>
        </label>
      </div>
      <div class="decision-data" hidden>${details.map((line) => `<p>${escapeHtml(line)}</p>`).join('')}</div>
    </section>`;
  }

  function verdictButton(verdict, current) {
    const active = verdict === current ? ' active' : '';
    return `<button type="button" class="verdict-button${active}" data-verdict-choice="${escapeHtml(verdict)}">${escapeHtml(labels[verdict])}</button>`;
  }

  function allDecisionPanels() {
    return Array.from(document.querySelectorAll('.decision-review'));
  }

  function setVerdict(panel, verdict) {
    if (!panel) {
      return;
    }
    const id = panel.dataset.decisionId;
    reviews[id] = { ...reviewFor(id), verdict };
    saveReviews();
    panel.dataset.verdict = verdict;
    panel.querySelector('.review-state-pill').className = `review-state-pill ${verdict}`;
    panel.querySelector('.review-state-pill').textContent = labels[verdict] || labels.pending;
    panel.querySelectorAll('.verdict-button').forEach((button) => {
      button.classList.toggle('active', button.dataset.verdictChoice === verdict);
    });
    updateReviewStats();
    applyReviewFilters();
  }

  function setFeedback(panel, field, value) {
    if (!panel) {
      return;
    }
    const id = panel.dataset.decisionId;
    reviews[id] = { ...reviewFor(id), [field]: value };
    saveReviews();
  }

  function updateReviewStats() {
    const panels = allDecisionPanels();
    const reviewed = panels.filter((panel) => reviewFor(panel.dataset.decisionId).verdict !== 'pending').length;
    const disagree = panels.filter((panel) => reviewFor(panel.dataset.decisionId).verdict === 'disagree').length;
    const question = panels.filter((panel) => reviewFor(panel.dataset.decisionId).verdict === 'question').length;
    const percent = panels.length ? reviewed / panels.length * 100 : 0;
    document.getElementById('reviewProgressText').textContent = `已审核 ${reviewed} / ${panels.length}`;
    document.getElementById('reviewIssueCount').textContent = `不赞同 ${disagree} · 有疑问 ${question}`;
    document.getElementById('reviewProgressFill').style.width = `${percent.toFixed(1)}%`;
  }

  function applyReviewFilters() {
    const actionFilter = document.getElementById('actionFilter')?.value || 'all';
    const reviewFilter = document.getElementById('reviewFilter')?.value || 'all';
    document.querySelectorAll('.turn').forEach((turn) => {
      let visiblePanels = 0;
      turn.querySelectorAll('.decision-review').forEach((panel) => {
        const review = reviewFor(panel.dataset.decisionId);
        const actionMatches = actionFilter === 'all' || panel.dataset.actionType === actionFilter;
        const reviewMatches = reviewFilter === 'all' || review.verdict === reviewFilter;
        const hidden = !(actionMatches && reviewMatches);
        panel.classList.toggle('review-hidden', hidden);
        if (!hidden) {
          visiblePanels += 1;
        }
      });
      turn.classList.toggle('review-hidden', visiblePanels === 0);
    });
  }

  function nextUnreviewed() {
    const next = allDecisionPanels().find((panel) => reviewFor(panel.dataset.decisionId).verdict === 'pending');
    if (!next) {
      return;
    }
    document.getElementById('actionFilter').value = 'all';
    document.getElementById('reviewFilter').value = 'all';
    const card = next.closest('.replay-card');
    if (card) {
      window.showReplay(Number(card.dataset.replayIndex));
    }
    applyReviewFilters();
    next.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  function generatePrompt() {
    const entries = allDecisionPanels()
      .map((panel) => ({ panel, review: reviewFor(panel.dataset.decisionId) }))
      .filter((entry) => entry.review.verdict === 'disagree' || entry.review.verdict === 'question');
    const disagree = entries.filter((entry) => entry.review.verdict === 'disagree');
    const question = entries.filter((entry) => entry.review.verdict === 'question');
    const lines = [
      '请根据以下 JanQ 随机模拟复盘意见，仔细思考并更新策略。',
      '',
      `我标记了 ${disagree.length} 个不赞同决定，以及 ${question.length} 个有疑问决定。`,
      '',
      '## 明确反对',
    ];
    appendPromptEntries(lines, disagree, '明确反对', '我不赞同的理由', '我建议的决定');
    lines.push('', '## 有疑问');
    appendPromptEntries(lines, question, '有疑问', '我的疑问', '我目前倾向的决定');
    const prompt = lines.join('\n').replace(/\n{3,}/g, '\n\n');
    document.getElementById('promptOutput').value = prompt;
    document.getElementById('promptSummary').textContent = `已生成：不赞同 ${disagree.length}，有疑问 ${question.length}`;
  }

  function appendPromptEntries(lines, entries, heading, reasonLabel, alternativeLabel) {
    if (!entries.length) {
      lines.push('- 无');
      return;
    }
    entries.forEach(({ panel, review }, index) => {
      const turn = panel.closest('.turn');
      const card = panel.closest('.replay-card');
      const title = panel.querySelector('h4')?.textContent || '';
      const summary = panel.querySelector('.decision-review-head p')?.textContent || '';
      const details = Array.from(panel.querySelectorAll('.decision-data p')).map((node) => node.textContent);
      lines.push(
        `### ${heading} ${index + 1}：样本 #${replayNumber(card)} / 第 ${turn?.dataset.turnNumber || '?'} 球 / ${title}`,
        `- 当前决定：${summary}`,
        ...details.map((line) => `- ${line}`),
        `- ${reasonLabel}：${review.reason || '（未填写）'}`,
        `- ${alternativeLabel}：${review.alternative || '（未填写）'}`,
        '',
      );
    });
  }

  async function copyPrompt() {
    const output = document.getElementById('promptOutput');
    if (!output.value.trim()) {
      generatePrompt();
    }
    output.select();
    try {
      await navigator.clipboard.writeText(output.value);
    } catch (_error) {
      document.execCommand('copy');
    }
  }

  ensureReviewPanels();
  updateReviewStats();
  applyReviewFilters();

  const baseShowReplay = window.showReplay;
  window.showReplay = function showReplayWithReview(index) {
    baseShowReplay(index);
    applyReviewFilters();
  };

  document.addEventListener('click', (event) => {
    const verdict = event.target.closest('[data-verdict-choice]');
    if (verdict) {
      setVerdict(verdict.closest('[data-decision-id]'), verdict.dataset.verdictChoice);
    }
  });
  document.addEventListener('input', (event) => {
    if (!event.target.matches('[data-field]')) {
      return;
    }
    setFeedback(event.target.closest('[data-decision-id]'), event.target.dataset.field, event.target.value);
  });
  document.getElementById('actionFilter').addEventListener('change', applyReviewFilters);
  document.getElementById('reviewFilter').addEventListener('change', applyReviewFilters);
  document.getElementById('nextUnreviewed').addEventListener('click', nextUnreviewed);
  document.getElementById('generatePrompt').addEventListener('click', generatePrompt);
  document.getElementById('copyPrompt').addEventListener('click', copyPrompt);
})();
"""


if __name__ == "__main__":
    main()
