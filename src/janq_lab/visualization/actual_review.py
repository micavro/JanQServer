"""Generate an interactive review page from an actual JanQ bot session."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
import json
from pathlib import Path
from typing import Any

from janq_lab.assets.nyukyu import AREA_COUNT, EXPECTED_WEIGHT_SUM, NyukyuTable, load_tables
from janq_lab.tiles import TILE_NAMES
from janq_lab.visualization.html_replay import (
    _CSS as REPLAY_BASE_CSS,
    _JS as REPLAY_BASE_JS,
    _area_probability_data,
    _asset_css,
    discover_tile_image_assets,
)


@dataclass
class ActualDecision:
    id: str
    hand_index: int
    order: int
    timestamp: str
    state: dict[str, Any]
    action: dict[str, Any]
    reason: str
    strategy: str
    area_decision: dict[str, Any] | None
    discard_decision: dict[str, Any] | None
    execution: dict[str, Any] | None = None
    confirmed: bool = False
    probe_line: int | None = None

    @property
    def action_kind(self) -> str:
        return str(self.action.get("kind") or "unknown")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "handIndex": self.hand_index,
            "order": self.order,
            "timestamp": self.timestamp,
            "state": self.state,
            "action": self.action,
            "actionKind": self.action_kind,
            "actionLabel": _action_label(self.action, self.state),
            "reason": self.reason,
            "strategy": self.strategy,
            "areaDecision": self.area_decision,
            "discardDecision": self.discard_decision,
            "execution": self.execution,
            "confirmed": self.confirmed,
            "probeLine": self.probe_line,
        }


@dataclass
class ActualTurn:
    id: str
    turn: int
    shot: ActualDecision
    discard: ActualDecision | None
    hand_before: list[int]
    hand_after_draw: list[int]
    hand_after_discard: list[int]
    drawn_tile: int | None
    discard_tile: int | None
    discard_index: int | None
    discard_source: str
    balls_before: int | None
    balls_after_draw: int | None
    riichi_before: bool
    riichi_declared: bool
    replay: bool
    agari: bool
    fourth_copy: bool
    shot_event_line: int | None
    tsumo_event_line: int | None
    discard_event_line: int | None
    outcome_event_line: int | None
    event_matches_decision: bool | None
    probability_data: dict[str, list[dict[str, object]]]
    area_scores: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        area = self.shot.area_decision or {}
        discard = self.discard.discard_decision if self.discard else None
        return {
            "id": self.id,
            "turn": self.turn,
            "shot": self.shot.to_dict(),
            "discard": self.discard.to_dict() if self.discard else None,
            "handBefore": self.hand_before,
            "handAfterDraw": self.hand_after_draw,
            "handAfterDiscard": self.hand_after_discard,
            "drawnTile": self.drawn_tile,
            "discardTile": self.discard_tile,
            "discardIndex": self.discard_index,
            "discardSource": self.discard_source,
            "ballsBefore": self.balls_before,
            "ballsAfterDraw": self.balls_after_draw,
            "riichiBefore": self.riichi_before,
            "riichiDeclared": self.riichi_declared,
            "replay": self.replay,
            "agari": self.agari,
            "fourthCopy": self.fourth_copy,
            "shotEventLine": self.shot_event_line,
            "tsumoEventLine": self.tsumo_event_line,
            "discardEventLine": self.discard_event_line,
            "outcomeEventLine": self.outcome_event_line,
            "eventMatchesDecision": self.event_matches_decision,
            "area": _as_int(self.shot.action.get("area")),
            "targetTiles": _tile_list(area.get("target_tiles")),
            "targetWeight": _as_int(area.get("target_weight")),
            "targetProbability": area.get("probability"),
            "areaReason": str(area.get("reason") or self.shot.reason),
            "areaScores": self.area_scores,
            "probabilityData": self.probability_data,
            "discardReason": str((discard or {}).get("reason") or ""),
            "shantenAfter": (discard or {}).get("shanten_after"),
            "accepts": _tile_list((discard or {}).get("accepts")),
        }


@dataclass
class ActualHand:
    index: int
    mode: str = "Unknown"
    status: str = "Unknown"
    initial_hand: list[int] = field(default_factory=list)
    final_hand: list[int] = field(default_factory=list)
    dora: int | None = None
    ura_dora: int | None = None
    start_mjchip: int | None = None
    end_mjchip: int | None = None
    bet_cost: int | None = None
    event_start_line: int | None = None
    event_end_line: int | None = None
    outcome: dict[str, Any] | None = None
    decisions: list[ActualDecision] = field(default_factory=list)
    turns: list[ActualTurn] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        decisions = [decision.to_dict() for decision in self.decisions]
        action_counts = Counter(decision["actionKind"] for decision in decisions)
        riichi_count = sum(
            bool(decision["action"].get("richi"))
            for decision in decisions
            if decision["actionKind"] == "discard"
        )
        outcome_type = str((self.outcome or {}).get("type") or "incomplete")
        outcome_labels = {
            "ryukyoku": "流局",
            "agari": "和牌",
            "result": "结算",
            "incomplete": "未完成",
        }
        return {
            "index": self.index,
            "mode": self.mode,
            "modeLabel": _mode_label(self.mode, self.status),
            "status": self.status,
            "initialHand": self.initial_hand,
            "finalHand": self.final_hand,
            "dora": self.dora,
            "uraDora": self.ura_dora,
            "startMjchip": self.start_mjchip,
            "endMjchip": self.end_mjchip,
            "deltaMjchip": _difference(self.end_mjchip, self.start_mjchip),
            "betCost": self.bet_cost,
            "eventStartLine": self.event_start_line,
            "eventEndLine": self.event_end_line,
            "outcome": self.outcome,
            "outcomeType": outcome_type,
            "outcomeLabel": outcome_labels.get(outcome_type, outcome_type),
            "decisionCount": len(decisions),
            "shotCount": action_counts["shot"],
            "discardCount": action_counts["discard"],
            "systemCount": action_counts["press_main"] + action_counts["agari"],
            "riichiCount": riichi_count,
            "turnCount": len(self.turns),
            "decisions": decisions,
            "turns": [turn.to_dict() for turn in self.turns],
            "systemDecisions": [
                decision
                for decision in decisions
                if decision["actionKind"] in ("press_main", "agari")
            ],
        }


def load_actual_report(
    session_path: str | Path,
    *,
    events_path: str | Path | None = None,
    events_start_line: int | None = None,
    events_end_line: int | None = None,
) -> dict[str, Any]:
    session_file = Path(session_path)
    session_rows = _read_jsonl(session_file)
    hands = _parse_session(session_rows)
    event_rows = _load_event_rows(
        events_path,
        start_line=events_start_line,
        end_line=events_end_line,
    )
    table = _load_base_table()
    _enrich_hands_from_events(hands, event_rows, table)
    _assign_actual_bet_costs(hands)

    hand_dicts = [hand.to_dict() for hand in hands]
    decision_count = sum(hand["decisionCount"] for hand in hand_dicts)
    bot_action_counts = Counter(
        decision["actionKind"]
        for hand in hand_dicts
        for decision in hand["decisions"]
    )
    completed = sum(hand["outcomeType"] != "incomplete" for hand in hand_dicts)
    wins = sum(hand["outcomeType"] in ("agari", "result") for hand in hand_dicts)
    draws = sum(hand["outcomeType"] == "ryukyoku" for hand in hand_dicts)
    first_chip = next(
        (hand["startMjchip"] for hand in hand_dicts if hand["startMjchip"] is not None),
        None,
    )
    last_chip = next(
        (
            hand["endMjchip"]
            for hand in reversed(hand_dicts)
            if hand["endMjchip"] is not None
        ),
        None,
    )
    strategy = next(
        (
            decision["strategy"]
            for hand in hand_dicts
            for decision in hand["decisions"]
            if decision["strategy"]
        ),
        "unknown",
    )
    session_started = next(
        (row.get("ts") for row in session_rows if row.get("type") == "bot_session_start"),
        None,
    )
    session_ended = session_rows[-1].get("ts") if session_rows else None
    failures = sum(
        1
        for row in session_rows
        if row.get("type") in ("bot_pause", "bot_action_failed")
    )
    event_stats = _event_statistics(event_rows, missing=events_path is not None and not event_rows)
    table_meta = {
        "name": table.name if table else None,
        "sha256": table.sha256 if table else None,
    }

    return {
        "meta": {
            "title": "JanQ 实测牌局决策复盘",
            "sessionId": session_file.stem,
            "sessionPath": str(session_file.resolve()),
            "eventsPath": str(Path(events_path).resolve()) if events_path else None,
            "eventsStartLine": events_start_line,
            "eventsEndLine": events_end_line,
            "generatedAt": datetime.now().astimezone().isoformat(),
            "sessionStartedAt": session_started,
            "sessionEndedAt": session_ended,
            "strategy": strategy,
            "probabilityTable": table_meta,
        },
        "summary": {
            "hands": len(hand_dicts),
            "completedHands": completed,
            "wins": wins,
            "draws": draws,
            "decisions": decision_count,
            "turns": sum(hand["turnCount"] for hand in hand_dicts),
            "botShots": bot_action_counts["shot"],
            "botDiscards": bot_action_counts["discard"],
            "botSystemActions": bot_action_counts["press_main"]
            + bot_action_counts["agari"],
            "botRiichiDeclarations": sum(hand["riichiCount"] for hand in hand_dicts),
            "sessionFailures": failures,
            "startMjchip": first_chip,
            "endMjchip": last_chip,
            "deltaMjchip": _difference(last_chip, first_chip),
            "actualBetCost": sum(
                hand["betCost"] or 0
                for hand in hand_dicts
                if hand["betCost"] is not None
            ),
            "eventStats": event_stats,
        },
        "hands": hand_dicts,
    }


def render_actual_review_html(
    report: dict[str, Any],
    *,
    resource_dir: str | Path | None = None,
    output_path: str | Path | None = None,
) -> str:
    report_json = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
    report_json = report_json.replace("</", "<\\/")
    assets = discover_tile_image_assets(
        resource_dir=resource_dir,
        output_path=output_path,
    )
    return (
        _HTML_TEMPLATE.replace("__TITLE__", escape(str(report["meta"]["title"])))
        .replace("__REPLAY_BASE_CSS__", REPLAY_BASE_CSS)
        .replace("__REVIEW_CSS__", _REVIEW_CSS)
        .replace("__TILE_ASSET_CSS__", _asset_css(assets))
        .replace("__REPORT_JSON__", report_json)
        .replace("__REPLAY_BASE_JS__", REPLAY_BASE_JS)
    )


def write_actual_review_html(
    report: dict[str, Any],
    output: str | Path,
    *,
    resource_dir: str | Path | None = None,
) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_actual_review_html(
            report,
            resource_dir=resource_dir,
            output_path=path,
        ),
        encoding="utf-8",
    )
    return path


def _parse_session(rows: list[dict[str, Any]]) -> list[ActualHand]:
    hands: dict[int, ActualHand] = {}
    latest_state: dict[str, Any] = {}
    pending: ActualDecision | None = None
    hand_orders: Counter[int] = Counter()

    for row in rows:
        row_type = row.get("type")
        payload = row.get("payload") or {}
        if row_type in ("bot_state", "bot_bootstrap_state"):
            latest_state = deepcopy(payload)
            hand_index = _as_int(payload.get("hand_index"))
            if hand_index and hand_index > 0:
                hand = hands.setdefault(hand_index, ActualHand(index=hand_index))
                _apply_state_to_hand(hand, payload)
            continue

        if row_type == "bot_decision":
            state_key = payload.get("state_key") or []
            hand_index = _state_key_int(state_key, 6) or _as_int(
                latest_state.get("hand_index")
            )
            if not hand_index or hand_index <= 0 or not payload.get("action"):
                continue
            hand = hands.setdefault(hand_index, ActualHand(index=hand_index))
            _apply_state_to_hand(hand, latest_state)
            hand_orders[hand_index] += 1
            pending = ActualDecision(
                id=f"h{hand_index}-d{hand_orders[hand_index]}",
                hand_index=hand_index,
                order=hand_orders[hand_index],
                timestamp=str(row.get("ts") or ""),
                state=_decision_state(latest_state, state_key),
                action=deepcopy(payload.get("action") or {}),
                reason=str(payload.get("reason") or ""),
                strategy=str(payload.get("strategy") or ""),
                area_decision=deepcopy(payload.get("area_decision")),
                discard_decision=deepcopy(payload.get("discard_decision")),
            )
            hand.decisions.append(pending)
            continue

        if row_type == "bot_action_done" and pending is not None:
            pending.execution = deepcopy(payload)
            continue

        if row_type == "bot_confirmed" and pending is not None:
            pending.confirmed = True
            pending.probe_line = _as_int(payload.get("probe_line"))

    return [hands[index] for index in sorted(hands)]


def _apply_state_to_hand(hand: ActualHand, state: dict[str, Any]) -> None:
    if not state:
        return
    if state.get("mode"):
        hand.mode = str(state["mode"])
    if state.get("status"):
        hand.status = str(state["status"])
    if state.get("dora") is not None and hand.dora is None:
        hand.dora = _as_int(state.get("dora"))
    if state.get("ura_dora") is not None and hand.ura_dora is None:
        hand.ura_dora = _as_int(state.get("ura_dora"))

    hand_tiles = _tile_list(state.get("hand"))
    last_event = state.get("last_event_type")
    if last_event == "recv_game_haipai":
        hand.initial_hand = hand_tiles
        hand.dora = _as_int(state.get("dora"))
        hand.ura_dora = _as_int(state.get("ura_dora"))
        hand.start_mjchip = _nested_int(state, "currency", "mjchip")
    if last_event in ("send_ryukyoku", "recv_janq_result"):
        hand.final_hand = hand_tiles
        hand.end_mjchip = _nested_int(state, "currency", "mjchip")
        result = deepcopy(state.get("last_result") or {})
        if last_event == "send_ryukyoku":
            result["type"] = "ryukyoku"
        elif "type" not in result:
            result["type"] = "result"
        hand.outcome = result

    if hand.start_mjchip is None:
        hand.start_mjchip = _nested_int(state, "currency", "mjchip")
    if hand.outcome is not None:
        hand.end_mjchip = _nested_int(state, "currency", "mjchip")


def _decision_state(
    latest_state: dict[str, Any],
    state_key: list[Any] | tuple[Any, ...],
) -> dict[str, Any]:
    state = {
        "phase": _state_key_value(state_key, 0) or latest_state.get("phase"),
        "mode": _state_key_value(state_key, 1) or latest_state.get("mode"),
        "status": _state_key_value(state_key, 2) or latest_state.get("status"),
        "balls": _state_key_int(state_key, 3),
        "hand": _tile_list(_state_key_value(state_key, 4)),
        "isReach": bool(_state_key_value(state_key, 5)),
        "handIndex": _state_key_int(state_key, 6),
        "completedHands": _state_key_int(state_key, 7),
        "dora": _as_int(latest_state.get("dora")),
        "uraDora": _as_int(latest_state.get("ura_dora")),
        "gameState": latest_state.get("game_state"),
        "mainButton": latest_state.get("main_button"),
        "lastProbeLine": _as_int(latest_state.get("last_line")),
    }
    if state["balls"] is None:
        state["balls"] = _as_int(latest_state.get("balls"))
    if not state["hand"]:
        state["hand"] = _tile_list(latest_state.get("hand"))
    return state


def _load_event_rows(
    path: str | Path | None,
    *,
    start_line: int | None,
    end_line: int | None,
) -> list[dict[str, Any]]:
    if path is None:
        return []
    event_path = Path(path)
    if not event_path.is_file():
        return []
    return _read_jsonl(
        event_path,
        start_line=start_line,
        end_line=end_line,
    )


def _load_base_table() -> NyukyuTable | None:
    try:
        return load_tables()["nyukyu_base_table.bytes"]
    except (FileNotFoundError, OSError, ValueError):
        return None


def _enrich_hands_from_events(
    hands: list[ActualHand],
    rows: list[dict[str, Any]],
    table: NyukyuTable | None,
) -> None:
    segments = _event_hand_segments(rows)
    for hand, segment in zip(hands, segments):
        _apply_event_segment_to_hand(hand, segment)
        hand.turns = _build_actual_turns(hand, segment, table)
        if hand.turns:
            last_turn = hand.turns[-1]
            hand.final_hand = (
                last_turn.hand_after_discard
                or last_turn.hand_after_draw
                or hand.final_hand
            )

    for hand in hands[len(segments) :]:
        hand.turns = _build_actual_turns(hand, [], table)


def _event_hand_segments(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    starts = [
        index
        for index, row in enumerate(rows)
        if row.get("type") == "recv_game_haipai"
    ]
    segments: list[list[dict[str, Any]]] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(rows)
        segments.append(rows[start:end])
    return segments


def _apply_event_segment_to_hand(
    hand: ActualHand,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    hand.event_start_line = _as_int(rows[0].get("_line"))
    hand.event_end_line = _as_int(rows[-1].get("_line"))
    haipai = next(
        (row for row in rows if row.get("type") == "recv_game_haipai"),
        None,
    )
    if haipai:
        payload = haipai.get("payload") or {}
        hand.initial_hand = _event_tile_list(payload.get("haipai")) or hand.initial_hand
        hand.dora = _event_tile(payload.get("omoDora", payload.get("omo_dora")))
        hand.ura_dora = _event_tile(payload.get("uraDora", payload.get("ura_dora")))
        hand.start_mjchip = _as_int(payload.get("mjchip"))
        if payload.get("status"):
            hand.status = str(payload["status"])
            hand.mode = _mode_from_status(hand.status)

    for row in rows:
        payload = row.get("payload") or {}
        chip = _as_int(payload.get("mjchip"))
        if chip is not None:
            hand.end_mjchip = chip
        if row.get("type") == "send_ryukyoku":
            hand.outcome = {"type": "ryukyoku", **deepcopy(payload)}
        elif row.get("type") == "recv_janq_result":
            hand.outcome = {"type": "result", **deepcopy(payload)}


def _build_actual_turns(
    hand: ActualHand,
    rows: list[dict[str, Any]],
    table: NyukyuTable | None,
) -> list[ActualTurn]:
    decisions = hand.decisions
    shot_positions = [
        index
        for index, decision in enumerate(decisions)
        if decision.action_kind == "shot"
    ]
    turns: list[ActualTurn] = []
    max_line = max((_as_int(row.get("_line")) or 0 for row in rows), default=0)

    for turn_number, decision_index in enumerate(shot_positions, 1):
        shot = decisions[decision_index]
        next_position = (
            shot_positions[turn_number]
            if turn_number < len(shot_positions)
            else len(decisions)
        )
        between = decisions[decision_index + 1 : next_position]
        bot_discard = next(
            (decision for decision in between if decision.action_kind == "discard"),
            None,
        )
        start_line = shot.probe_line or _as_int(shot.state.get("lastProbeLine")) or 0
        next_shot = (
            decisions[next_position]
            if next_position < len(decisions)
            and decisions[next_position].action_kind == "shot"
            else None
        )
        end_line = (
            next_shot.probe_line
            if next_shot and next_shot.probe_line is not None
            else max_line + 1
        )
        window = [
            row
            for row in rows
            if start_line <= (_as_int(row.get("_line")) or -1) < end_line
        ]
        tsumo = next(
            (row for row in window if row.get("type") == "recv_game_tsumo"),
            None,
        )
        tsumo_line = _as_int(tsumo.get("_line")) if tsumo else None
        server_discard = next(
            (
                row
                for row in window
                if row.get("type") == "send_action_dahai"
                and (
                    tsumo_line is None
                    or (_as_int(row.get("_line")) or -1) > tsumo_line
                )
            ),
            None,
        )
        outcome_event = next(
            (
                row
                for row in window
                if row.get("type") in ("send_ryukyoku", "recv_janq_result")
            ),
            None,
        )
        tsumo_payload = (tsumo or {}).get("payload") or {}
        discard_payload = (server_discard or {}).get("payload") or {}

        hand_before = _tile_list(shot.state.get("hand"))
        hand_after_draw = _event_tile_list(tsumo_payload.get("tehai"))
        if not hand_after_draw and bot_discard is not None:
            hand_after_draw = _tile_list(bot_discard.state.get("hand"))
        drawn_tile = _event_tile(tsumo_payload.get("pai"))
        if drawn_tile is None and len(hand_after_draw) > len(hand_before):
            drawn_tile = hand_after_draw[-1]

        discard_tile = _event_tile(discard_payload.get("pai"))
        discard_index = _as_int(discard_payload.get("pos"))
        if discard_tile is None and bot_discard is not None:
            discard_tile = _as_int(bot_discard.action.get("discard_tile"))
            one_based = _as_int(bot_discard.action.get("discard_index"))
            discard_index = one_based - 1 if one_based and one_based > 0 else None

        hand_after_discard = _remove_discard(
            hand_after_draw,
            discard_index=discard_index,
            discard_tile=discard_tile,
        )
        discard_source = (
            "bot"
            if bot_discard is not None
            else "automatic"
            if server_discard is not None
            else "none"
        )
        event_matches_decision: bool | None = None
        if bot_discard is not None and server_discard is not None:
            event_matches_decision = (
                discard_tile == _as_int(bot_discard.action.get("discard_tile"))
                and bool(discard_payload.get("richi"))
                == bool(bot_discard.action.get("richi"))
            )

        area = shot.area_decision or {}
        targets = _tile_list(area.get("target_tiles"))
        area_scores = {
            str(area_index): sum(
                table.tile_weight(area_index, tile_id)
                for tile_id in targets
            )
            for area_index in range(1, AREA_COUNT + 1)
        } if table else {}
        probability_data = (
            _area_probability_data(tuple(hand_before), table)
            if table
            else {}
        )
        riichi_declared = bool(discard_payload.get("richi")) or bool(
            bot_discard and bot_discard.action.get("richi")
        )

        turns.append(
            ActualTurn(
                id=f"h{hand.index}-t{turn_number}",
                turn=turn_number,
                shot=shot,
                discard=bot_discard,
                hand_before=hand_before,
                hand_after_draw=hand_after_draw,
                hand_after_discard=hand_after_discard,
                drawn_tile=drawn_tile,
                discard_tile=discard_tile,
                discard_index=discard_index,
                discard_source=discard_source,
                balls_before=_as_int(shot.state.get("balls")),
                balls_after_draw=_as_int(tsumo_payload.get("zandan"))
                if tsumo
                else _as_int((bot_discard.state if bot_discard else {}).get("balls")),
                riichi_before=bool(shot.state.get("isReach")),
                riichi_declared=riichi_declared,
                replay=bool(tsumo_payload.get("replay")),
                agari=bool(tsumo_payload.get("agari")),
                fourth_copy=(
                    drawn_tile is not None and hand_before.count(drawn_tile) >= 3
                ),
                shot_event_line=shot.probe_line,
                tsumo_event_line=tsumo_line,
                discard_event_line=_as_int(server_discard.get("_line"))
                if server_discard
                else None,
                outcome_event_line=_as_int(outcome_event.get("_line"))
                if outcome_event
                else None,
                event_matches_decision=event_matches_decision,
                probability_data=probability_data,
                area_scores=area_scores,
            )
        )
    return turns


def _assign_actual_bet_costs(hands: list[ActualHand]) -> None:
    for index, hand in enumerate(hands):
        if index == 0:
            hand.bet_cost = 0
            continue
        previous = hands[index - 1]
        if previous.start_mjchip is None or hand.start_mjchip is None:
            hand.bet_cost = None
            continue
        hand.bet_cost = max(0, previous.start_mjchip - hand.start_mjchip)


def _remove_discard(
    tiles: list[int],
    *,
    discard_index: int | None,
    discard_tile: int | None,
) -> list[int]:
    if not tiles or discard_tile is None:
        return list(tiles)
    result = list(tiles)
    if (
        discard_index is not None
        and 0 <= discard_index < len(result)
        and result[discard_index] == discard_tile
    ):
        result.pop(discard_index)
        return result
    try:
        result.remove(discard_tile)
    except ValueError:
        pass
    return result


def _event_statistics(
    rows: list[dict[str, Any]],
    *,
    missing: bool = False,
) -> dict[str, Any]:
    if missing:
        return {"missing": True}
    counts = Counter(str(row.get("type") or "") for row in rows)
    failures = [
        {
            "line": row.get("_line"),
            "type": row.get("type"),
            "payload": row.get("payload"),
        }
        for row in rows
        if row.get("type") in ("bridge_command_failed", "bot_pause")
    ]
    return {
        "rows": len(rows),
        "haipai": counts["recv_game_haipai"],
        "tsumo": counts["recv_game_tsumo"],
        "shots": counts["send_action_shot"],
        "discards": counts["send_action_dahai"],
        "serverRiichiDiscards": sum(
            bool((row.get("payload") or {}).get("richi"))
            for row in rows
            if row.get("type") == "send_action_dahai"
        ),
        "draws": counts["send_ryukyoku"],
        "results": counts["recv_janq_result"],
        "bets": counts["send_action_start"],
        "bridgeCommands": counts["bridge_command_received"],
        "bridgeCompletions": counts["bridge_command_completed"],
        "failures": failures,
    }


def _read_jsonl(
    path: Path,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if start_line is not None and line_number < start_line:
                continue
            if end_line is not None and line_number > end_line:
                break
            if not line.strip():
                continue
            row = json.loads(line)
            row["_line"] = line_number
            rows.append(row)
    return rows


def _action_label(action: dict[str, Any], state: dict[str, Any]) -> str:
    kind = action.get("kind")
    if kind == "shot":
        return f"发射区域 {action.get('area')}"
    if kind == "discard":
        tile = _tile_label(_as_int(action.get("discard_tile")))
        suffix = "并立直" if action.get("richi") else ""
        return f"弃 {tile}{suffix}"
    if kind == "agari":
        return "按和牌"
    if kind == "press_main":
        phase = state.get("phase")
        if phase == "bet_wait":
            return "按 BET"
        if phase == "free_wait":
            return "按 FREE"
        if phase == "agari_wait":
            return "按和牌"
        return "按主按钮"
    return str(kind or "未知动作")


def _mode_from_status(status: str) -> str:
    return {
        "NORMAL": "Normal",
        "PARENCHAN": "ParenChallenge",
        "YAKUMAN": "YakumanBonus",
    }.get(status.upper(), status)


def _mode_label(mode: str, status: str) -> str:
    value = (mode or status).lower()
    if "yakuman" in value:
        return "役满奖励游戏"
    if "paren" in value:
        return "普通奖励游戏"
    return "普通游戏"


def _tile_label(tile_id: int | None) -> str:
    if tile_id is None or not 0 <= tile_id < len(TILE_NAMES):
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
    suit_labels = {"m": "万", "s": "索", "p": "饼"}
    return f"{name[:-1]}{suit_labels[name[-1]]}"


def _event_tile(value: Any) -> int | None:
    event_id = _as_int(value)
    if event_id is None:
        return None
    tile_id = event_id - 1
    return tile_id if 0 <= tile_id < 34 else None


def _event_tile_list(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        tile_id = _event_tile(item)
        if tile_id is not None:
            result.append(tile_id)
    return result


def _tile_list(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        tile_id = _as_int(item)
        if tile_id is not None and 0 <= tile_id < 34:
            result.append(tile_id)
    return result


def _state_key_value(state_key: list[Any] | tuple[Any, ...], index: int) -> Any:
    return state_key[index] if len(state_key) > index else None


def _state_key_int(state_key: list[Any] | tuple[Any, ...], index: int) -> int | None:
    return _as_int(_state_key_value(state_key, index))


def _nested_int(payload: dict[str, Any], key: str, nested_key: str) -> int | None:
    nested = payload.get(key)
    if not isinstance(nested, dict):
        return None
    return _as_int(nested.get(nested_key))


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _difference(end: int | None, start: int | None) -> int | None:
    if end is None or start is None:
        return None
    return end - start


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate an interactive review HTML from an actual JanQ session."
    )
    parser.add_argument("session_path", help="Bot session JSONL path")
    parser.add_argument("--events-path", help="Probe event JSONL path")
    parser.add_argument("--events-start-line", type=int)
    parser.add_argument("--events-end-line", type=int)
    parser.add_argument(
        "--resource-dir",
        help="Directory containing color00.png for real tile artwork.",
    )
    parser.add_argument(
        "--output",
        default=str(Path("_runtime") / "replays" / "janq_actual_review.html"),
    )
    args = parser.parse_args(argv)
    report = load_actual_report(
        args.session_path,
        events_path=args.events_path,
        events_start_line=args.events_start_line,
        events_end_line=args.events_end_line,
    )
    output = write_actual_review_html(
        report,
        args.output,
        resource_dir=args.resource_dir,
    )
    print(output)


_REVIEW_CSS = r"""
.review-toolbar {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) auto;
  gap: 16px;
  align-items: center;
}

.review-progress {
  display: grid;
  gap: 7px;
}

.review-progress-line,
.toolbar-actions,
.decision-review-head,
.event-verification,
.system-action-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.progress-track {
  height: 7px;
  overflow: hidden;
  border-radius: 4px;
  background: #e4e9e7;
}

.progress-fill {
  width: 0;
  height: 100%;
  background: var(--teal);
}

.toolbar-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
}

select,
textarea,
.command-button,
.verdict-button {
  border: 1px solid var(--line);
  background: #fff;
  color: var(--ink);
  font: inherit;
}

select,
.command-button {
  min-height: 38px;
  padding: 7px 10px;
  border-radius: 6px;
}

.command-button {
  cursor: pointer;
  font-weight: 700;
}

.command-button.primary {
  border-color: var(--teal);
  background: var(--teal);
  color: #fff;
}

.hand-button-meta {
  display: block;
  margin-top: 7px;
  color: var(--muted);
  font-size: 12px;
}

.mode-pill,
.review-state-pill,
.source-pill {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 3px 7px;
  border-radius: 5px;
  font-size: 12px;
  font-weight: 700;
}

.mode-pill.normal,
.source-pill.bot {
  background: #e7f5f2;
  color: var(--teal-dark);
}

.mode-pill.paren {
  background: #e8f1f9;
  color: var(--blue);
}

.mode-pill.yakuman {
  background: #f8e8e8;
  color: var(--red);
}

.source-pill.automatic {
  background: #fff3dc;
  color: #8b5d12;
}

.source-pill.none {
  background: #eef1f0;
  color: var(--muted);
}

.hand-summary-grid,
.event-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-top: 12px;
}

.hand-summary-grid div,
.event-grid div {
  min-width: 0;
  padding: 9px 10px;
  border-top: 3px solid #a9c8c2;
  background: #f7f9f9;
}

.hand-summary-grid span,
.event-grid span {
  display: block;
  color: var(--muted);
  font-size: 12px;
}

.hand-summary-grid b,
.event-grid b {
  display: block;
  margin-top: 3px;
  overflow-wrap: anywhere;
}

.event-verification {
  margin-top: 12px;
  padding: 9px 10px;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  color: var(--muted);
  font-size: 12px;
}

.verification-ok {
  color: var(--green);
  font-weight: 700;
}

.verification-bad {
  color: var(--red);
  font-weight: 700;
}

.review-stack {
  display: grid;
  gap: 10px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.decision-review {
  min-width: 0;
  padding: 11px 0 0;
}

.decision-review + .decision-review {
  border-top: 1px dashed var(--line);
}

.decision-review-head h4 {
  margin: 0;
  font-size: 14px;
}

.decision-review-head > div {
  min-width: 0;
}

.decision-review-head p {
  margin: 3px 0 0;
  color: var(--muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.verdict-control {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 9px;
}

.verdict-button {
  min-height: 34px;
  padding: 6px 10px;
  border-radius: 6px;
  cursor: pointer;
}

.verdict-button.active[data-verdict-choice="agree"] {
  border-color: var(--green);
  background: #e9f6ee;
  color: var(--green);
}

.verdict-button.active[data-verdict-choice="question"] {
  border-color: var(--amber);
  background: #fff5df;
  color: #8b5d12;
}

.verdict-button.active[data-verdict-choice="disagree"] {
  border-color: var(--red);
  background: #f9eaea;
  color: var(--red);
}

.verdict-button.active[data-verdict-choice="pending"] {
  background: #edf0ef;
  color: #45504e;
}

.feedback-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-top: 9px;
}

.feedback-fields label {
  color: var(--muted);
  font-size: 12px;
}

textarea {
  display: block;
  width: 100%;
  min-height: 76px;
  margin-top: 5px;
  padding: 8px 9px;
  resize: vertical;
  border-radius: 6px;
  line-height: 1.5;
}

.automatic-note {
  color: var(--muted);
  font-size: 13px;
}

.system-actions {
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}

.system-actions > h3 {
  margin-bottom: 10px;
}

.system-action {
  padding: 11px 0;
  border-top: 1px dashed var(--line);
}

.system-action:first-of-type {
  border-top: 0;
}

.prompt-section {
  margin-top: 16px;
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
  box-shadow: var(--shadow);
}

.prompt-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
}

.prompt-head h2 {
  margin-bottom: 4px;
}

#promptOutput {
  min-height: 280px;
  margin-top: 12px;
  font-family: Consolas, "Microsoft YaHei", monospace;
  font-size: 13px;
}

.review-state-pill {
  background: #edf0ef;
  color: var(--muted);
}

.review-state-pill.question {
  background: #fff5df;
  color: #8b5d12;
}

.review-state-pill.disagree {
  background: #f9eaea;
  color: var(--red);
}

.review-state-pill.agree {
  background: #e9f6ee;
  color: var(--green);
}

@media (max-width: 980px) {
  .review-toolbar,
  .feedback-fields,
  .hand-summary-grid,
  .event-grid {
    grid-template-columns: 1fr;
  }

  .toolbar-actions,
  .prompt-head,
  .decision-review-head,
  .event-verification,
  .system-action-head {
    align-items: flex-start;
    justify-content: flex-start;
    flex-direction: column;
  }

  .decision-review-head > div {
    width: 100%;
  }
}
"""


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
__REPLAY_BASE_CSS__
__REVIEW_CSS__
__TILE_ASSET_CSS__
</style>
</head>
<body>
<main class="shell">
  <section class="hero">
    <div>
      <p class="eyebrow">JanQ Actual Session Review</p>
      <h1 id="pageTitle">JanQ 实测牌局决策复盘</h1>
      <p class="subtle" id="sessionMeta"></p>
      <p class="source" id="scopeLine"></p>
    </div>
    <div class="stat-strip" id="statStrip"></div>
  </section>

  <section class="panel economy-panel" id="economySummary"></section>
  <section class="panel strategy-panel">
    <h2>复盘方式</h2>
    <p>按原回放结构查看每一球：先审区域与概率，再核对实际摸牌、弃牌、立直和牌面流转。发射与主动弃牌分别审核；立直后的自动摸切只展示真实事件，不伪造机器人决策。</p>
  </section>

  <section class="panel review-toolbar">
    <div class="review-progress">
      <div class="review-progress-line">
        <b id="reviewProgressText">已审核 0 / 0</b>
        <span id="reviewIssueCount">不赞同 0 · 有疑问 0</span>
      </div>
      <div class="progress-track"><div class="progress-fill" id="progressFill"></div></div>
    </div>
    <div class="toolbar-actions">
      <select id="actionFilter" aria-label="动作筛选">
        <option value="all">全部动作</option>
        <option value="shot">发射</option>
        <option value="discard">弃牌</option>
        <option value="riichi">立直</option>
        <option value="system">BET / FREE / 和牌</option>
      </select>
      <select id="reviewFilter" aria-label="审核筛选">
        <option value="all">全部审核状态</option>
        <option value="pending">未审核</option>
        <option value="agree">赞同</option>
        <option value="question">有疑问</option>
        <option value="disagree">不赞同</option>
      </select>
      <button type="button" class="command-button" id="nextUnreviewed">下一个未审核</button>
    </div>
  </section>

  <section class="workspace">
    <aside class="example-panel">
      <div class="example-panel-head">
        <h2>实测牌局</h2>
        <span id="handCount"></span>
      </div>
      <div class="example-list" id="handList"></div>
    </aside>
    <div class="replay-stage" id="reviewStage"></div>
  </section>

  <section class="prompt-section" id="promptSection">
    <div class="prompt-head">
      <div>
        <p class="eyebrow">Strategy Revision</p>
        <h2>策略修订 Prompt</h2>
        <p class="subtle" id="promptSummary">尚未生成</p>
      </div>
      <div class="toolbar-actions">
        <button type="button" class="command-button primary" id="generatePrompt">生成 Prompt</button>
        <button type="button" class="command-button" id="copyPrompt">复制</button>
      </div>
    </div>
    <textarea id="promptOutput" spellcheck="false"></textarea>
  </section>
</main>

<script id="reportData" type="application/json">__REPORT_JSON__</script>
<script>
__REPLAY_BASE_JS__

const REPORT = JSON.parse(document.getElementById("reportData").textContent);
const EXPECTED_WEIGHT_SUM = 10000;
const storageKey = `janq-review-v2:${REPORT.meta.sessionId}:${REPORT.meta.eventsEndLine ?? "session"}`;
let reviews = loadReviews();
let selectedHand = REPORT.hands[0]?.index ?? null;

const tileNames = [
  "1万","2万","3万","4万","5万","6万","7万","8万","9万",
  "1索","2索","3索","4索","5索","6索","7索","8索","9索",
  "1饼","2饼","3饼","4饼","5饼","6饼","7饼","8饼","9饼",
  "东","南","西","北","白","发","中"
];

function loadReviews() {
  try {
    return JSON.parse(localStorage.getItem(storageKey) || "{}");
  } catch {
    return {};
  }
}

function saveReviews() {
  localStorage.setItem(storageKey, JSON.stringify(reviews));
  updateProgress();
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function signed(value) {
  if (value === null || value === undefined) return "-";
  return Number(value) > 0 ? `+${value}` : String(value);
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", { hour12: false });
}

function tileClass(id) {
  if (id <= 8) return "man";
  if (id <= 17) return "sou";
  if (id <= 26) return "pin";
  return "honor";
}

function tileHtml(id, extraClass = "") {
  if (id === null || id === undefined) return '<span class="tile empty">-</span>';
  return `<span class="tile tile-art tile-id-${id} ${tileClass(id)} ${extraClass}" title="${esc(tileNames[id] ?? id)}">${esc(tileNames[id] ?? id)}</span>`;
}

function doraItemHtml(label, id) {
  const known = id !== null && id !== undefined;
  const name = known ? (tileNames[id] ?? id) : "未记录";
  return `<div class="dora-item ${known ? "known" : "unknown"}">
    <span class="dora-label">${esc(label)}</span>
    ${tileHtml(id)}
    <b>${esc(name)}</b>
  </div>`;
}

function doraStripHtml(hand) {
  return `<div class="dora-strip" aria-label="宝牌与里宝牌">
    ${doraItemHtml("宝牌", hand.dora)}
    ${doraItemHtml("里宝牌", hand.uraDora)}
  </div>`;
}

function tilesHtml(tiles, discardIndex = null, discardTile = null) {
  let usedFallback = false;
  return (tiles || []).map((id, index) => {
    let discarded = index === discardIndex;
    if (discardIndex === null && !usedFallback && discardTile !== null && id === discardTile) {
      discarded = true;
      usedFallback = true;
    }
    return tileHtml(id, discarded ? "discarded" : "");
  }).join("");
}

function miniTilesHtml(tiles) {
  return (tiles || []).map(id =>
    `<span class="mini-tile tile-art tile-id-${id} ${tileClass(id)}" title="${esc(tileNames[id] ?? id)}">${esc(tileNames[id] ?? id)}</span>`
  ).join("");
}

function reviewFor(id) {
  return reviews[id] || { verdict: "pending", reason: "", alternative: "" };
}

function allDecisions() {
  return REPORT.hands.flatMap(hand => hand.decisions);
}

function modeClass(hand) {
  const text = `${hand.mode} ${hand.status}`.toLowerCase();
  if (text.includes("yakuman")) return "yakuman";
  if (text.includes("paren")) return "paren";
  return "normal";
}

function renderSummary() {
  const s = REPORT.summary;
  const events = s.eventStats || {};
  const stats = [
    ["实测牌局", s.hands],
    ["真实回合", s.turns],
    ["和牌 / 流局", `${s.wins} / ${s.draws}`],
    ["MJChip", signed(s.deltaMjchip)]
  ];
  document.getElementById("statStrip").innerHTML = stats.map(([label, value]) =>
    `<span><b>${esc(value)}</b>${esc(label)}</span>`
  ).join("");
  document.getElementById("sessionMeta").textContent =
    `${REPORT.meta.strategy} · ${formatTime(REPORT.meta.sessionStartedAt)} · ` +
    `${events.shots ?? s.botShots} 次发射 · ${events.discards ?? s.botDiscards} 次服务器弃牌`;
  document.getElementById("scopeLine").textContent =
    `事件行 ${REPORT.meta.eventsStartLine ?? "-"}-${REPORT.meta.eventsEndLine ?? "-"} · ` +
    `概率表 ${REPORT.meta.probabilityTable?.name ?? "不可用"} · ` +
    `桥接 ${events.bridgeCompletions ?? "-"} / ${events.bridgeCommands ?? "-"} · ` +
    `失败 ${events.failures?.length ?? s.sessionFailures}`;
  const netClass = Number(s.deltaMjchip) >= 0 ? "positive" : "negative";
  document.getElementById("economySummary").innerHTML = `
    <div class="economy-head">
      <div>
        <p class="eyebrow">完整实测经济</p>
        <h2>MJChip ${esc(s.startMjchip ?? "-")} → ${esc(s.endMjchip ?? "-")} · <span class="${netClass}">净 ${esc(signed(s.deltaMjchip))}</span></h2>
        <p>事件范围内实际 BET ${esc(events.bets ?? "-")} 次 · 可观测投入 ${esc(s.actualBetCost ?? "-")} · 完成牌局 ${esc(s.completedHands)}</p>
      </div>
      <div class="economy-counts">
        <span>主动立直 <b>${esc(s.botRiichiDeclarations)}</b></span>
        <span>策略决策 <b>${esc(s.decisions)}</b></span>
        <span>执行失败 <b>${esc(events.failures?.length ?? s.sessionFailures)}</b></span>
      </div>
    </div>`;
}

function renderHandList() {
  document.getElementById("handCount").textContent = `${REPORT.hands.length} 局`;
  document.getElementById("handList").innerHTML = REPORT.hands.map(hand => {
    const reviewed = hand.decisions.filter(d => reviewFor(d.id).verdict !== "pending").length;
    const disagree = hand.decisions.filter(d => reviewFor(d.id).verdict === "disagree").length;
    const question = hand.decisions.filter(d => reviewFor(d.id).verdict === "question").length;
    const resultClass = hand.outcomeType === "ryukyoku" ? "lose" : "win";
    return `<button type="button" class="example-button ${hand.index === selectedHand ? "active" : ""}" data-hand-index="${hand.index}">
      <span class="example-top">
        <b>#${hand.index}</b>
        <span class="result-pill ${resultClass}">${esc(hand.outcomeLabel)}</span>
      </span>
      <span class="example-hand">${miniTilesHtml(hand.initialHand)}</span>
      <span class="example-meta"><span class="mode-pill ${modeClass(hand)}">${esc(hand.modeLabel)}</span>&nbsp; ${hand.turnCount} 球 · 立直 ${hand.riichiCount}</span>
      <span class="hand-button-meta">已审 ${reviewed}/${hand.decisionCount} · 不赞同 ${disagree} · 有疑问 ${question}</span>
    </button>`;
  }).join("");
}

function matchesDecision(decision) {
  if (!decision) return false;
  const actionFilter = document.getElementById("actionFilter").value;
  const reviewFilter = document.getElementById("reviewFilter").value;
  const review = reviewFor(decision.id);
  const system = decision.actionKind === "press_main" || decision.actionKind === "agari";
  const riichi = decision.actionKind === "discard" && Boolean(decision.action.richi);
  const actionMatches =
    actionFilter === "all" ||
    actionFilter === decision.actionKind ||
    (actionFilter === "system" && system) ||
    (actionFilter === "riichi" && riichi);
  return actionMatches && (reviewFilter === "all" || reviewFilter === review.verdict);
}

function turnMatches(turn) {
  return matchesDecision(turn.shot) || matchesDecision(turn.discard);
}

function renderSelectedHand() {
  const hand = REPORT.hands.find(item => item.index === selectedHand);
  if (!hand) {
    document.getElementById("reviewStage").innerHTML = '<div class="empty-state">没有可显示的牌局。</div>';
    return;
  }
  const turns = hand.turns.filter(turnMatches);
  const systems = hand.systemDecisions.filter(matchesDecision);
  document.getElementById("reviewStage").innerHTML = `
    <section class="replay-card active" data-replay-index="${hand.index}">
      <div class="replay-head">
        <div>
          <p class="eyebrow">Actual Hand #${hand.index}</p>
          <h2>${esc(hand.outcomeLabel)} · ${esc(hand.modeLabel)}</h2>
          <p class="subtle">${esc(hand.mode)} / ${esc(hand.status)} · 事件 ${esc(hand.eventStartLine ?? "-")}-${esc(hand.eventEndLine ?? "-")}</p>
          ${doraStripHtml(hand)}
        </div>
        <div class="score-box">MJChip ${esc(hand.startMjchip ?? "-")} → ${esc(hand.endMjchip ?? "-")}</div>
      </div>
      <section class="hand-panel">
        <h3>起手 <span class="aside">${esc(hand.modeLabel)}</span></h3>
        <div class="tiles">${tilesHtml(hand.initialHand)}</div>
        <div class="hand-summary-grid">
          <div><span>真实发射</span><b>${hand.shotCount}</b></div>
          <div><span>机器人弃牌</span><b>${hand.discardCount}</b></div>
          <div><span>主动立直</span><b>${hand.riichiCount}</b></div>
          <div><span>本局可观测投入</span><b>${esc(hand.betCost ?? "-")}</b></div>
        </div>
      </section>
      <section class="turn-list">
        ${turns.length ? turns.map(turnHtml).join("") : '<div class="empty-state">当前筛选下没有逐球决策。</div>'}
      </section>
      <section class="hand-panel">
        <h3>回合后 <span class="aside">${esc(hand.outcomeLabel)}</span></h3>
        <div class="tiles">${tilesHtml(hand.finalHand)}</div>
      </section>
      ${sessionEconomyHtml(hand)}
      ${systemActionsHtml(systems)}
      ${bonusStatusHtml(hand)}
    </section>`;
  initializeAreaPanels();
}

function areaBarHtml(turn) {
  const buttons = [];
  for (let area = 1; area <= 7; area += 1) {
    const selected = Number(turn.area) === area ? " selected" : "";
    const score = turn.areaScores?.[String(area)] ?? "-";
    buttons.push(`<button class="area${selected}" type="button" data-area="${area}" onclick="showArea(this)">
      <span class="area-label">区域 ${area}</span>
      <span class="area-score">${esc(score)}</span>
    </button>`);
  }
  return `<div class="area-bar">${buttons.join("")}</div>`;
}

function turnHtml(turn) {
  const discardText = turn.agari
    ? "和牌"
    : turn.discardTile !== null
      ? `弃 ${tileNames[turn.discardTile] ?? turn.discardTile}`
      : "无弃牌，回合结束";
  const sourceLabel = {
    bot: "机器人主动弃牌",
    automatic: "立直后游戏自动摸切",
    none: "无弃牌事件"
  }[turn.discardSource] || turn.discardSource;
  const riichiText = turn.riichiDeclared
    ? "本球宣告"
    : turn.riichiBefore
      ? "已立直"
      : "未立直";
  const accepts = (turn.accepts || []).map(id => tileNames[id]).join("、") || "无";
  const targets = (turn.targetTiles || []).map(id => tileNames[id]).join("、") || "无";
  const discardIndex = turn.discardSource === "bot" ? turn.discardIndex : null;
  const drawnDiscarded = turn.drawnTile !== null && turn.drawnTile === turn.discardTile;
  const handDiscard = drawnDiscarded ? null : turn.discardTile;
  return `<article class="turn" id="${esc(turn.id)}">
    <div class="turn-head">
      <div>
        <h3>第 ${turn.turn} 球</h3>
        <p>${esc(turn.areaReason)}</p>
      </div>
      <div class="balls">球数 ${esc(turn.ballsBefore ?? "-")} → ${esc(turn.ballsAfterDraw ?? "-")}</div>
    </div>
    ${areaBarHtml(turn)}
    <div class="probability-panel">
      <div class="probability-head">
        <h4>区域概率</h4>
        <span class="probability-caption">点击上面的区域查看当前手牌下的有效分布</span>
      </div>
      <div class="probability-table"></div>
    </div>
    <script type="application/json" class="prob-data">${JSON.stringify(turn.probabilityData || {}).replaceAll("</", "<\\/")}<\/script>
    <div class="turn-grid">
      <div class="turn-detail"><span>摸到</span><b>${esc(tileNames[turn.drawnTile] ?? "-")}</b></div>
      <div class="turn-detail"><span>处理</span><b>${esc(discardText)}</b></div>
      <div class="turn-detail"><span>保护</span><b>${turn.fourthCopy ? "第四张保护" : "无保护"}</b></div>
      <div class="turn-detail"><span>重抽</span><b>${turn.replay ? "是" : "否"}</b></div>
      <div class="turn-detail"><span>立直</span><b>${esc(riichiText)}</b></div>
    </div>
    <dl class="reason-list">
      <div><dt>目标牌</dt><dd>${esc(targets)}</dd></div>
      <div><dt>目标权重</dt><dd>${esc(turn.targetWeight ?? "-")} / ${EXPECTED_WEIGHT_SUM}</dd></div>
      <div><dt>舍牌理由</dt><dd>${esc(turn.discardReason || sourceLabel)}</dd></div>
      <div><dt>舍后受入</dt><dd>${esc(accepts)}${turn.shantenAfter !== null && turn.shantenAfter !== undefined ? ` · 向听 ${esc(turn.shantenAfter)}` : ""}</dd></div>
    </dl>
    <div class="hand-flow">
      <div class="flow-hand">
        <span>手牌 13 张 + 实际摸牌</span>
        <div class="hand-row">
          <div class="tiles small hand-tiles">${tilesHtml(turn.handBefore, null, handDiscard)}</div>
          <div class="drawn-tile ${drawnDiscarded ? "discarded" : ""}" title="摸到第14张">${tileHtml(turn.drawnTile, drawnDiscarded ? "discarded" : "")}</div>
        </div>
      </div>
    </div>
    <div class="event-verification">
      <span><span class="source-pill ${esc(turn.discardSource)}">${esc(sourceLabel)}</span></span>
      <span>shot ${esc(turn.shotEventLine ?? "-")} · tsumo ${esc(turn.tsumoEventLine ?? "-")} · dahai ${esc(turn.discardEventLine ?? "-")} · end ${esc(turn.outcomeEventLine ?? "-")}</span>
      ${turn.eventMatchesDecision === true ? '<span class="verification-ok">决策与服务器事件一致</span>' :
        turn.eventMatchesDecision === false ? '<span class="verification-bad">决策与服务器事件不一致</span>' : ""}
    </div>
    <div class="review-stack">
      ${decisionReviewHtml(turn.shot, "发射决策")}
      ${turn.discard ? decisionReviewHtml(turn.discard, turn.riichiDeclared ? "弃牌与立直决策" : "弃牌决策") :
        `<div class="automatic-note">${esc(sourceLabel)}，没有对应的机器人弃牌决策，因此这里不生成虚假的审核项。</div>`}
    </div>
  </article>`;
}

function decisionReviewHtml(decision, title) {
  const review = reviewFor(decision.id);
  return `<section class="decision-review" data-decision-id="${esc(decision.id)}" data-verdict="${esc(review.verdict)}">
    <div class="decision-review-head">
      <div>
        <h4>${esc(title)} · 决策 ${decision.order}</h4>
        <p>${esc(decision.actionLabel)} · ${esc(decision.reason)} · probe ${esc(decision.probeLine ?? "-")}</p>
      </div>
      <span class="review-state-pill ${esc(review.verdict)}">${esc(verdictLabel(review.verdict))}</span>
    </div>
    <div class="verdict-control" role="group" aria-label="${esc(title)}审核结论">
      ${verdictButton("pending", "未审核", review.verdict)}
      ${verdictButton("agree", "赞同", review.verdict)}
      ${verdictButton("question", "有疑问", review.verdict)}
      ${verdictButton("disagree", "不赞同", review.verdict)}
    </div>
    <div class="feedback-fields">
      <label>不同意的理由或具体疑问
        <textarea data-field="reason" placeholder="说明为什么不同意，或具体不确定什么。">${esc(review.reason)}</textarea>
      </label>
      <label>你认为应该怎么做
        <textarea data-field="alternative" placeholder="可选：写出更好的区域、弃牌或立直选择。">${esc(review.alternative)}</textarea>
      </label>
    </div>
  </section>`;
}

function verdictButton(value, label, selected) {
  return `<button type="button" class="verdict-button ${value === selected ? "active" : ""}" data-verdict-choice="${value}">${label}</button>`;
}

function verdictLabel(value) {
  return {
    pending: "未审核",
    agree: "赞同",
    question: "有疑问",
    disagree: "不赞同"
  }[value] || value;
}

function sessionEconomyHtml(hand) {
  const delta = hand.deltaMjchip;
  const netClass = Number(delta) >= 0 ? "positive" : "negative";
  return `<section class="session-economy">
    <div class="session-economy-head">
      <div>
        <p class="eyebrow">本局实际经济</p>
        <h3>MJChip ${esc(hand.startMjchip ?? "-")} → ${esc(hand.endMjchip ?? "-")} · <span class="${netClass}">${esc(signed(delta))}</span></h3>
      </div>
      <span class="session-bet">可观测投入 ${esc(hand.betCost ?? "-")}</span>
    </div>
    <div class="session-payout-grid">
      <div><span>发射回合</span><b>${hand.turnCount}</b></div>
      <div><span>服务器弃牌</span><b>${hand.turns.filter(t => t.discardEventLine !== null).length}</b></div>
      <div><span>自动摸切</span><b>${hand.turns.filter(t => t.discardSource === "automatic").length}</b></div>
      <div><span>结果</span><b>${esc(hand.outcomeLabel)}</b></div>
    </div>
  </section>`;
}

function systemActionsHtml(decisions) {
  if (!decisions.length) return "";
  return `<section class="system-actions">
    <h3>流程动作</h3>
    ${decisions.map(decision => `<div class="system-action">
      <div class="system-action-head">
        <div><b>${esc(decision.actionLabel)}</b><p class="subtle">${esc(decision.reason)} · probe ${esc(decision.probeLine ?? "-")}</p></div>
        <span class="${decision.confirmed ? "verification-ok" : "verification-bad"}">${decision.confirmed ? "服务器已确认" : "未确认"}</span>
      </div>
      ${decisionReviewHtml(decision, "流程决策")}
    </div>`).join("")}
  </section>`;
}

function bonusStatusHtml(hand) {
  if (modeClass(hand) === "paren") {
    return '<section class="bonus-chain"><div class="bonus-chain-head"><div><p class="eyebrow">奖励游戏进程</p><h2>本局为普通奖励游戏实测记录</h2></div></div></section>';
  }
  if (modeClass(hand) === "yakuman") {
    return '<section class="bonus-chain"><div class="bonus-chain-head"><div><p class="eyebrow">奖励游戏进程</p><h2>本局为役满奖励游戏实测记录</h2></div></div></section>';
  }
  return '<section class="bonus-empty">本局未进入奖励游戏；若后续实测日志包含奖励模式，会按同一套逐球结构显示。</section>';
}

function initializeAreaPanels() {
  document.querySelectorAll("#reviewStage .turn").forEach(turn => {
    const button = turn.querySelector(".area.selected") || turn.querySelector(".area");
    if (button) showArea(button);
  });
}

function setVerdict(decisionId, verdict) {
  reviews[decisionId] = { ...reviewFor(decisionId), verdict };
  saveReviews();
  renderHandList();
  renderSelectedHand();
  if (verdict === "disagree" || verdict === "question") {
    document.querySelector(`[data-decision-id="${CSS.escape(decisionId)}"] textarea[data-field="reason"]`)?.focus();
  }
}

function setFeedback(decisionId, field, value) {
  reviews[decisionId] = { ...reviewFor(decisionId), [field]: value };
  saveReviews();
}

function updateProgress() {
  const decisions = allDecisions();
  const reviewed = decisions.filter(d => reviewFor(d.id).verdict !== "pending").length;
  const disagree = decisions.filter(d => reviewFor(d.id).verdict === "disagree").length;
  const question = decisions.filter(d => reviewFor(d.id).verdict === "question").length;
  const percent = decisions.length ? reviewed / decisions.length * 100 : 0;
  document.getElementById("reviewProgressText").textContent = `已审核 ${reviewed} / ${decisions.length}`;
  document.getElementById("reviewIssueCount").textContent = `不赞同 ${disagree} · 有疑问 ${question}`;
  document.getElementById("progressFill").style.width = `${percent}%`;
}

function nextUnreviewed() {
  const next = allDecisions().find(decision => reviewFor(decision.id).verdict === "pending");
  if (!next) return;
  selectedHand = next.handIndex;
  document.getElementById("actionFilter").value = "all";
  document.getElementById("reviewFilter").value = "all";
  renderHandList();
  renderSelectedHand();
  requestAnimationFrame(() => {
    document.querySelector(`[data-decision-id="${CSS.escape(next.id)}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  });
}

function findTurnForDecision(hand, decisionId) {
  return hand.turns.find(turn => turn.shot?.id === decisionId || turn.discard?.id === decisionId) || null;
}

function generatePrompt() {
  const disagreements = [];
  const questions = [];
  for (const hand of REPORT.hands) {
    for (const decision of hand.decisions) {
      const review = reviewFor(decision.id);
      const entry = { hand, decision, review, turn: findTurnForDecision(hand, decision.id) };
      if (review.verdict === "disagree") disagreements.push(entry);
      if (review.verdict === "question") questions.push(entry);
    }
  }
  const lines = [
    `请根据以下 JanQ 实测复盘意见，更新当前 ${REPORT.meta.strategy} 策略。`,
    "",
    "实测范围：",
    `- 会话：${REPORT.meta.sessionId}`,
    `- 事件行：${REPORT.meta.eventsStartLine ?? "-"}-${REPORT.meta.eventsEndLine ?? "-"}`,
    `- 牌局：${REPORT.summary.hands} 局；真实发射回合 ${REPORT.summary.turns}`,
    `- 和牌 ${REPORT.summary.wins}；流局 ${REPORT.summary.draws}；主动立直 ${REPORT.summary.botRiichiDeclarations}`,
    `- MJChip：${REPORT.summary.startMjchip ?? "-"} → ${REPORT.summary.endMjchip ?? "-"}，净 ${signed(REPORT.summary.deltaMjchip)}`,
    "",
    "修改要求：",
    "1. 逐条判断我的反对意见或疑问是否成立，并说明依据。",
    "2. 若成立，把修正落实到明确的策略条件、优先级或 EV 比较中，不要只为单个牌例打补丁。",
    "3. 区分发射区域决策、弃牌决策、立直决策和流程动作；不要把游戏自动摸切误认为机器人决策。",
    "4. 修改代码后补充针对这些真实局面的自动化测试，并说明可能影响的其他路线。",
    "5. 保留日志中的牌 ID、中文牌名、事件行和真实牌面流转，不要猜测缺失状态。",
    "",
    `我标记了 ${disagreements.length} 个不赞同决定，以及 ${questions.length} 个有疑问决定。`
  ];
  appendFeedback(lines, disagreements, "明确反对", "我不赞同的理由", "我建议的决定");
  appendFeedback(lines, questions, "有疑问", "我的疑问", "我目前倾向的决定");
  const prompt = lines.join("\n").replace(/\n{3,}/g, "\n\n");
  document.getElementById("promptOutput").value = prompt;
  document.getElementById("promptSummary").textContent =
    `已生成：${disagreements.length} 条反对意见，${questions.length} 条疑问`;
}

function appendFeedback(lines, entries, heading, reasonLabel, alternativeLabel) {
  entries.forEach(({ hand, decision, review, turn }, index) => {
    const state = decision.state || {};
    const area = decision.areaDecision;
    const discard = decision.discardDecision;
    lines.push(
      "",
      `## ${heading} ${index + 1}：第 ${hand.index} 局 / 决策 ${decision.order}`,
      `- 模式：${hand.modeLabel}；mode=${state.mode}; status=${state.status}; phase=${state.phase}`,
      `- 当时手牌：${(state.hand || []).map(id => `${tileNames[id]}(id=${id})`).join(" ")}`,
      `- 剩余球数：${state.balls ?? "-"}；已立直=${state.isReach}`,
      `- 当前决定：${decision.actionLabel}`,
      `- 当前策略理由：${decision.reason}`,
      area ? `- 区域详情：area=${area.area}; probability=${area.probability}; targets=${(area.target_tiles || []).map(id => `${tileNames[id]}(${id})`).join(", ")}` : "",
      discard ? `- 弃牌详情：discard=${tileNames[discard.discard_tile] ?? discard.discard_tile}; shanten_after=${discard.shanten_after}; accepts=${(discard.accepts || []).map(id => `${tileNames[id]}(${id})`).join(", ")}; riichi=${discard.declare_riichi}` : "",
      turn ? `- 实际结果：摸到=${tileNames[turn.drawnTile] ?? "-"}; 弃牌=${tileNames[turn.discardTile] ?? "-"}; source=${turn.discardSource}; shotLine=${turn.shotEventLine ?? "-"}; tsumoLine=${turn.tsumoEventLine ?? "-"}; discardLine=${turn.discardEventLine ?? "-"}` : "",
      `- 执行：success=${decision.execution?.success ?? "unknown"}; confirmed=${decision.confirmed}; probeLine=${decision.probeLine ?? "-"}`,
      `- ${reasonLabel}：${review.reason || "（未填写）"}`,
      `- ${alternativeLabel}：${review.alternative || "（未填写）"}`
    );
  });
}

async function copyPrompt() {
  const output = document.getElementById("promptOutput");
  if (!output.value) generatePrompt();
  try {
    await navigator.clipboard.writeText(output.value);
  } catch {
    output.focus();
    output.select();
    document.execCommand("copy");
  }
  const button = document.getElementById("copyPrompt");
  button.textContent = "已复制";
  setTimeout(() => button.textContent = "复制", 1200);
}

document.addEventListener("click", event => {
  const handButton = event.target.closest("[data-hand-index]");
  if (handButton) {
    selectedHand = Number(handButton.dataset.handIndex);
    renderHandList();
    renderSelectedHand();
    window.scrollTo({ top: document.querySelector(".workspace").offsetTop - 12, behavior: "smooth" });
    return;
  }
  const verdict = event.target.closest("[data-verdict-choice]");
  if (verdict) {
    const reviewPanel = verdict.closest("[data-decision-id]");
    setVerdict(reviewPanel.dataset.decisionId, verdict.dataset.verdictChoice);
  }
});

document.addEventListener("input", event => {
  if (!event.target.matches("textarea[data-field]")) return;
  const reviewPanel = event.target.closest("[data-decision-id]");
  setFeedback(reviewPanel.dataset.decisionId, event.target.dataset.field, event.target.value);
});

document.getElementById("actionFilter").addEventListener("change", renderSelectedHand);
document.getElementById("reviewFilter").addEventListener("change", renderSelectedHand);
document.getElementById("nextUnreviewed").addEventListener("click", nextUnreviewed);
document.getElementById("generatePrompt").addEventListener("click", generatePrompt);
document.getElementById("copyPrompt").addEventListener("click", copyPrompt);

renderSummary();
renderHandList();
renderSelectedHand();
updateProgress();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
