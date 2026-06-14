from __future__ import annotations

import json
from pathlib import Path
import tempfile

from janq_lab.visualization.actual_review import (
    load_actual_report,
    render_actual_review_html,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _decision(
    *,
    kind: str,
    balls: int,
    hand: list[int],
    area: int | None = None,
    discard_index: int | None = None,
    discard_tile: int | None = None,
    richi: bool = False,
    is_reach: bool = False,
) -> dict:
    action = {
        "kind": kind,
        "area": area,
        "discard_index": discard_index,
        "discard_tile": discard_tile,
        "richi": richi,
    }
    return {
        "action": action,
        "reason": f"{kind}_reason",
        "strategy": "route_ev",
        "state_key": [
            "shoot_wait" if kind == "shot" else "user_wait",
            "Normal",
            "NORMAL",
            balls,
            hand,
            is_reach,
            1,
            0,
        ],
        "area_decision": {
            "area": area,
            "target_tiles": [1, 2],
            "target_weight": 2000,
            "probability": 0.2,
            "reason": "normal_next_area",
        } if kind == "shot" else None,
        "discard_decision": {
            "is_agari": False,
            "discard_tile": discard_tile,
            "shanten_after": 0,
            "accepts": [3, 4],
            "reason": "test_discard",
            "declare_riichi": richi,
        } if kind == "discard" else None,
    }


def test_actual_report_builds_real_turns_and_auto_discard() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = root / "session.jsonl"
        events = root / "events.jsonl"
        initial = list(range(13))
        first_draw = initial + [21]
        after_discard = list(range(12)) + [21]
        second_draw = after_discard + [4]
        initial_state = {
            "phase": "shoot_wait",
            "mode": "Normal",
            "status": "NORMAL",
            "balls": 2,
            "hand": initial,
            "dora": 20,
            "ura_dora": 3,
            "is_reach": False,
            "hand_index": 1,
            "completed_hands": 0,
            "last_line": 1,
            "last_event_type": "recv_game_haipai",
            "currency": {"mjchip": 100, "start_mjchip": 100},
            "last_result": None,
        }
        shot_one = _decision(kind="shot", balls=2, hand=initial, area=3)
        discard = _decision(
            kind="discard",
            balls=1,
            hand=first_draw,
            discard_index=13,
            discard_tile=12,
            richi=True,
        )
        shot_two = _decision(
            kind="shot",
            balls=1,
            hand=after_discard,
            area=2,
            is_reach=True,
        )
        end_state = {
            **initial_state,
            "phase": "result",
            "balls": 0,
            "hand": second_draw,
            "is_reach": True,
            "completed_hands": 1,
            "last_line": 8,
            "last_event_type": "send_ryukyoku",
            "last_result": {"type": "ryukyoku"},
        }
        session_rows = [
            {"ts": "2026-01-01T00:00:00Z", "type": "bot_session_start", "payload": {}},
            {"ts": "2026-01-01T00:00:01Z", "type": "bot_state", "payload": initial_state},
        ]
        for decision, probe_line in ((shot_one, 2), (discard, 4), (shot_two, 5)):
            session_rows.extend(
                [
                    {"ts": "2026-01-01T00:00:02Z", "type": "bot_decision", "payload": decision},
                    {
                        "ts": "2026-01-01T00:00:03Z",
                        "type": "bot_action_done",
                        "payload": {"success": True, "action": decision["action"]},
                    },
                    {
                        "ts": "2026-01-01T00:00:04Z",
                        "type": "bot_confirmed",
                        "payload": {
                            "probe_line": probe_line,
                            "action": decision["action"],
                        },
                    },
                ]
            )
        session_rows.append(
            {"ts": "2026-01-01T00:00:05Z", "type": "bot_state", "payload": end_state}
        )
        _write_jsonl(session, session_rows)
        _write_jsonl(
            events,
            [
                {
                    "type": "recv_game_haipai",
                    "payload": {
                        "haipai": [tile + 1 for tile in initial],
                        "omoDora": 21,
                        "uraDora": 4,
                        "zandan": 2,
                        "status": "NORMAL",
                        "mjchip": 100,
                    },
                },
                {"type": "send_action_shot", "payload": {"area": 3}},
                {
                    "type": "recv_game_tsumo",
                    "payload": {
                        "pai": 22,
                        "tehai": [tile + 1 for tile in first_draw],
                        "zandan": 1,
                        "richi": False,
                    },
                },
                {
                    "type": "send_action_dahai",
                    "payload": {"pos": 12, "pai": 13, "richi": True},
                },
                {"type": "send_action_shot", "payload": {"area": 2}},
                {
                    "type": "recv_game_tsumo",
                    "payload": {
                        "pai": 5,
                        "tehai": [tile + 1 for tile in second_draw],
                        "zandan": 0,
                        "richi": True,
                    },
                },
                {
                    "type": "send_action_dahai",
                    "payload": {"pos": 13, "pai": 5, "richi": True},
                },
                {"type": "send_ryukyoku", "payload": {}},
            ],
        )

        report = load_actual_report(
            session,
            events_path=events,
            events_start_line=1,
            events_end_line=8,
        )

    hand = report["hands"][0]
    assert report["summary"]["hands"] == 1
    assert report["summary"]["draws"] == 1
    assert report["summary"]["turns"] == 2
    assert hand["outcomeLabel"] == "流局"
    assert hand["turns"][0]["drawnTile"] == 21
    assert hand["turns"][0]["discardTile"] == 12
    assert hand["turns"][0]["discardSource"] == "bot"
    assert hand["turns"][0]["eventMatchesDecision"] is True
    assert hand["turns"][0]["riichiDeclared"] is True
    assert hand["turns"][1]["discardSource"] == "automatic"
    assert hand["turns"][1]["discard"] is None
    assert len(hand["turns"][0]["probabilityData"]) in (0, 7)


def test_actual_review_html_contains_original_ui_and_feedback_controls() -> None:
    report = {
        "meta": {
            "title": "Test",
            "sessionId": "session",
            "eventsEndLine": 10,
            "strategy": "route_ev",
        },
        "summary": {
            "hands": 0,
            "wins": 0,
            "draws": 0,
            "decisions": 0,
            "turns": 0,
            "botRiichiDeclarations": 0,
            "sessionFailures": 0,
            "eventStats": {},
        },
        "hands": [],
    }

    html = render_actual_review_html(report)

    assert "不同意的理由" in html
    assert "有疑问" in html
    assert "生成 Prompt" in html
    assert "localStorage" in html
    assert "我不赞同的理由" in html
    assert "我的疑问" in html
    assert "点击上面的区域查看当前手牌下的有效分布" in html
    assert "function showArea(button)" in html
    assert "class=\"example-panel\"" in html
    assert "class=\"replay-stage\"" in html
    assert "function doraStripHtml(hand)" in html
    assert "宝牌与里宝牌" in html
    assert "里宝牌" in html


def test_actual_review_html_can_embed_real_tile_art() -> None:
    report = {
        "meta": {
            "title": "Test",
            "sessionId": "session",
            "eventsEndLine": 10,
            "strategy": "route_ev",
        },
        "summary": {
            "hands": 0,
            "wins": 0,
            "draws": 0,
            "decisions": 0,
            "turns": 0,
            "botRiichiDeclarations": 0,
            "sessionFailures": 0,
            "eventStats": {},
        },
        "hands": [],
    }

    html = render_actual_review_html(
        report,
        resource_dir=Path(__file__).parents[1] / "allresourse",
    )

    assert ".tile-id-0" in html
    assert ".tile-id-33" in html
    assert "data:image/png;base64" in html
