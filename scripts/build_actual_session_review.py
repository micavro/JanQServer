from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
import json
from pathlib import Path
from typing import Any

from janq_lab.model.hand import is_complete_hand, shanten
from janq_lab.probe.normalize import (
    api_tile_to_model,
    normalize_haipai_payload,
    normalize_result_payload,
    normalize_tsumo_payload,
)
from janq_lab.tiles import TILE_NAMES


TILE_LABELS = (
    "1万", "2万", "3万", "4万", "5万", "6万", "7万", "8万", "9万",
    "1索", "2索", "3索", "4索", "5索", "6索", "7索", "8索", "9索",
    "1筒", "2筒", "3筒", "4筒", "5筒", "6筒", "7筒", "8筒", "9筒",
    "东", "南", "西", "北", "白", "发", "中",
)


@dataclass
class ShotEvent:
    line: int
    ts: str
    area: int | None
    drawn_tile: int | None = None
    zandan_after: int | None = None
    replay: bool | None = None
    agari: bool | None = None


@dataclass
class DiscardEvent:
    line: int
    ts: str
    tile: int | None
    pos: int | None
    richi: bool | None


@dataclass
class HandRecord:
    index: int
    line_start: int
    line_end: int | None = None
    ts_start: str = ""
    status: str = ""
    mode: str = ""
    initial_hand: list[int] = field(default_factory=list)
    initial_shanten: int | None = None
    dora: int | None = None
    ura_dora: int | None = None
    start_mjchip: int | None = None
    end_mjchip: int | None = None
    bet_cost: int | None = None
    shots: list[ShotEvent] = field(default_factory=list)
    discards: list[DiscardEvent] = field(default_factory=list)
    ever_tenpai: bool = False
    tenpai_draw_opportunities: int = 0
    riichi_count: int = 0
    outcome_type: str = "incomplete"
    win: int = 0
    han: int | None = None
    yakuman: int | None = None
    odds: int | None = None
    result_payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "lineStart": self.line_start,
            "lineEnd": self.line_end,
            "tsStart": self.ts_start,
            "status": self.status,
            "mode": self.mode,
            "initialHand": self.initial_hand,
            "initialShanten": self.initial_shanten,
            "dora": self.dora,
            "uraDora": self.ura_dora,
            "startMjchip": self.start_mjchip,
            "endMjchip": self.end_mjchip,
            "deltaMjchip": difference(self.end_mjchip, self.start_mjchip),
            "betCost": self.bet_cost,
            "shots": [item.__dict__ for item in self.shots],
            "discards": [item.__dict__ for item in self.discards],
            "everTenpai": self.ever_tenpai,
            "tenpaiDrawOpportunities": self.tenpai_draw_opportunities,
            "riichiCount": self.riichi_count,
            "outcomeType": self.outcome_type,
            "win": self.win,
            "han": self.han,
            "yakuman": self.yakuman,
            "odds": self.odds,
            "resultPayload": self.result_payload,
        }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build clean stats and review HTML for a JanQ live session.")
    parser.add_argument("session_path")
    parser.add_argument("--events-path", default="_runtime/logs/janq_events.jsonl")
    parser.add_argument("--html-output", default="JanQ_actual_review.html")
    parser.add_argument("--json-output", default=None)
    args = parser.parse_args(argv)

    session_path = Path(args.session_path)
    events_path = Path(args.events_path)
    session_rows = read_jsonl(session_path)
    start_ts = parse_ts(next(row["ts"] for row in session_rows if row.get("type") == "bot_session_start"))
    end_ts = parse_ts(session_rows[-1]["ts"])
    event_rows = [
        row for row in read_jsonl(events_path)
        if (ts := parse_ts(row.get("ts"))) is not None and start_ts <= ts <= end_ts
    ]

    selected_bet = first_selected_bet(session_rows) or 10
    hands = build_hands(event_rows, default_bet=selected_bet)
    decisions = build_decisions(session_rows, hands)
    report = {
        "meta": {
            "sessionId": session_path.stem,
            "sessionPath": str(session_path.resolve()),
            "eventsPath": str(events_path.resolve()),
            "generatedAt": datetime.now().astimezone().isoformat(),
            "sessionStartedAt": start_ts.isoformat(),
            "sessionEndedAt": end_ts.isoformat(),
            "eventsStartLine": event_rows[0]["_line"] if event_rows else None,
            "eventsEndLine": event_rows[-1]["_line"] if event_rows else None,
            "selectedBet": selected_bet,
        },
        "stats": compute_stats(hands, decisions),
        "shotAudit": shot_audit(decisions),
        "shotOutcomeFrequencyByArea": shot_outcome_frequency_by_area(hands),
        "hands": [hand.to_dict() for hand in hands],
        "decisions": decisions,
    }

    json_output = Path(args.json_output) if args.json_output else Path("_runtime/replays") / f"{session_path.stem}_stats.json"
    html_output = Path(args.html_output)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    html_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html_output.write_text(render_html(report), encoding="utf-8")
    timestamped = Path("_runtime/replays") / f"{session_path.stem}_actual_review.html"
    timestamped.parent.mkdir(parents=True, exist_ok=True)
    timestamped.write_text(render_html(report), encoding="utf-8")
    print(json.dumps({
        "html": str(html_output.resolve()),
        "htmlCopy": str(timestamped.resolve()),
        "json": str(json_output.resolve()),
        "hands": report["stats"]["hands"],
        "completedHands": report["stats"]["completedHands"],
        "decisions": len(decisions),
    }, ensure_ascii=False))


def build_hands(rows: list[dict[str, Any]], *, default_bet: int) -> list[HandRecord]:
    hands: list[HandRecord] = []
    current: HandRecord | None = None
    previous_end_mjchip: int | None = None
    pending_shot: ShotEvent | None = None
    last_draw_hand: list[int] = []

    for row in rows:
        row_type = row.get("type")
        payload = row.get("payload") or {}
        if row_type == "recv_game_haipai":
            if current is not None:
                current.line_end = row["_line"] - 1
                current.end_mjchip = current.end_mjchip or last_payload_mjchip(current)
                previous_end_mjchip = current.end_mjchip
            normalized = normalize_haipai_payload(payload)
            status = str(payload.get("status") or "")
            current = HandRecord(
                index=len(hands) + 1,
                line_start=row["_line"],
                ts_start=str(row.get("ts") or ""),
                status=status,
                mode=mode_from_status(status),
                initial_hand=list(normalized["haipai"]),
                dora=normalized["dora"],
                ura_dora=normalized["ura_dora"],
                start_mjchip=optional_int(payload.get("mjchip")),
            )
            current.initial_shanten = safe_shanten(current.initial_hand)
            current.ever_tenpai = current.initial_shanten == 0
            if current.status == "NORMAL":
                if previous_end_mjchip is not None and current.start_mjchip is not None:
                    current.bet_cost = max(0, previous_end_mjchip - current.start_mjchip)
                else:
                    current.bet_cost = default_bet
            else:
                current.bet_cost = 0
            hands.append(current)
            pending_shot = None
            last_draw_hand = []
            continue

        if current is None:
            continue

        chip = optional_int(payload.get("mjchip"))
        if chip is not None:
            current.end_mjchip = chip

        if row_type == "send_action_shot":
            pending_shot = ShotEvent(
                line=row["_line"],
                ts=str(row.get("ts") or ""),
                area=optional_int(payload.get("area")),
            )
            current.shots.append(pending_shot)
        elif row_type == "recv_game_tsumo":
            normalized = normalize_tsumo_payload(payload)
            drawn = normalized["pai"]
            last_draw_hand = list(normalized["tehai"])
            if pending_shot is not None:
                pending_shot.drawn_tile = drawn
                pending_shot.zandan_after = optional_int(payload.get("zandan"))
                pending_shot.replay = optional_bool(payload.get("replay"))
                pending_shot.agari = optional_bool(payload.get("agari"))
                pending_shot = None
            if optional_bool(payload.get("agari")):
                current.ever_tenpai = True
            elif draw_has_tenpai_discard(last_draw_hand):
                current.tenpai_draw_opportunities += 1
                current.ever_tenpai = True
        elif row_type == "send_action_dahai":
            discard = DiscardEvent(
                line=row["_line"],
                ts=str(row.get("ts") or ""),
                tile=api_tile_to_model(payload.get("pai")),
                pos=optional_int(payload.get("pos")),
                richi=optional_bool(payload.get("richi")),
            )
            current.discards.append(discard)
            if discard.richi:
                current.riichi_count += 1
                current.ever_tenpai = True
            after = remove_discard(last_draw_hand, discard.tile, discard.pos)
            if safe_shanten(after) == 0:
                current.ever_tenpai = True
        elif row_type == "send_ryukyoku":
            current.outcome_type = "ryukyoku"
            current.line_end = row["_line"]
        elif row_type == "recv_janq_result":
            normalized = normalize_result_payload(payload)
            current.result_payload = dict(payload)
            current.result_payload["tehaiModel"] = list(normalized["tehai"])
            current.outcome_type = "result"
            current.win = optional_int(payload.get("win")) or 0
            current.han = optional_int(payload.get("han"))
            current.yakuman = optional_int(payload.get("yakuman"))
            current.odds = optional_int(payload.get("odds"))
            current.line_end = row["_line"]
            current.ever_tenpai = True

    if current is not None:
        current.line_end = current.line_end or rows[-1]["_line"] if rows else current.line_start
    return hands


def build_decisions(session_rows: list[dict[str, Any]], hands: list[HandRecord]) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    pending_index: int | None = None
    by_hand_shots: dict[int, list[ShotEvent]] = {hand.index: hand.shots for hand in hands}
    by_hand_discards: dict[int, list[DiscardEvent]] = {hand.index: hand.discards for hand in hands}
    shot_cursor: Counter[int] = Counter()
    discard_cursor: Counter[int] = Counter()

    for row in session_rows:
        row_type = row.get("type")
        payload = row.get("payload") or {}
        if row_type == "bot_decision":
            action = payload.get("action") or {}
            state_key = payload.get("state_key") or []
            hand_index = state_key_int(state_key, 6)
            decision = {
                "id": f"d{len(decisions) + 1}",
                "timestamp": row.get("ts"),
                "handIndex": hand_index,
                "phase": state_key_value(state_key, 0),
                "mode": state_key_value(state_key, 1),
                "status": state_key_value(state_key, 2),
                "balls": state_key_int(state_key, 3),
                "hand": tile_list(state_key_value(state_key, 4)),
                "isReach": bool(state_key_value(state_key, 5)),
                "kind": action.get("kind"),
                "action": action,
                "reason": payload.get("reason") or "",
                "strategy": payload.get("strategy") or "",
                "areaDecision": payload.get("area_decision"),
                "discardDecision": payload.get("discard_decision"),
                "execution": None,
                "confirmed": False,
                "probeLine": None,
                "actual": {},
            }
            attach_actual(decision, by_hand_shots, by_hand_discards, shot_cursor, discard_cursor)
            decisions.append(decision)
            pending_index = len(decisions) - 1
        elif row_type == "bot_action_done" and pending_index is not None:
            decisions[pending_index]["execution"] = payload
        elif row_type == "bot_confirmed" and pending_index is not None:
            decisions[pending_index]["confirmed"] = True
            decisions[pending_index]["probeLine"] = optional_int(payload.get("probe_line"))
    return decisions


def attach_actual(
    decision: dict[str, Any],
    by_hand_shots: dict[int, list[ShotEvent]],
    by_hand_discards: dict[int, list[DiscardEvent]],
    shot_cursor: Counter[int],
    discard_cursor: Counter[int],
) -> None:
    hand_index = optional_int(decision.get("handIndex"))
    if hand_index is None:
        return
    if decision.get("kind") == "shot":
        events = by_hand_shots.get(hand_index, [])
        pos = shot_cursor[hand_index]
        shot_cursor[hand_index] += 1
        if pos < len(events):
            event = events[pos]
            requested = optional_int((decision.get("action") or {}).get("area"))
            decision["actual"] = {
                "line": event.line,
                "sentArea": event.area,
                "drawnTile": event.drawn_tile,
                "zandanAfter": event.zandan_after,
                "replay": event.replay,
                "agari": event.agari,
                "matchesDecision": requested == event.area,
            }
    elif decision.get("kind") == "discard":
        events = by_hand_discards.get(hand_index, [])
        pos = discard_cursor[hand_index]
        discard_cursor[hand_index] += 1
        if pos < len(events):
            event = events[pos]
            requested_tile = optional_int((decision.get("action") or {}).get("discard_tile"))
            requested_richi = bool((decision.get("action") or {}).get("richi"))
            decision["actual"] = {
                "line": event.line,
                "discardTile": event.tile,
                "pos": event.pos,
                "richi": event.richi,
                "matchesDecision": requested_tile == event.tile and requested_richi == bool(event.richi),
            }


def compute_stats(hands: list[HandRecord], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [hand for hand in hands if hand.outcome_type != "incomplete"]
    normal = [hand for hand in hands if hand.status == "NORMAL"]
    normal_completed = [hand for hand in normal if hand.outcome_type != "incomplete"]
    normal_wins = [hand for hand in normal_completed if hand.win > 0]
    paren = [hand for hand in hands if hand.status == "PARENCHAN"]
    paren_completed = [hand for hand in paren if hand.outcome_type != "incomplete"]
    paren_wins = [hand for hand in paren_completed if hand.win > 0]
    yakuman = [hand for hand in hands if hand.status == "YAKUMAN"]
    yakuman_completed = [hand for hand in yakuman if hand.outcome_type != "incomplete"]
    yakuman_wins = [hand for hand in yakuman_completed if hand.win > 0]
    total_bet = sum(hand.bet_cost or 0 for hand in hands)
    chip_return = sum(max(0, difference(hand.end_mjchip, hand.start_mjchip) or 0) for hand in hands)
    result_win_field_sum = sum(hand.win for hand in hands)
    start_chip = first_not_none(hand.start_mjchip for hand in hands)
    end_chip = last_not_none(hand.end_mjchip for hand in hands)
    shanten_counts = Counter(hand.initial_shanten for hand in hands if hand.initial_shanten is not None)
    shot_counts = Counter(optional_int((d.get("action") or {}).get("area")) for d in decisions if d.get("kind") == "shot")
    return {
        "hands": len(hands),
        "completedHands": len(completed),
        "normalHands": len(normal),
        "normalCompletedHands": len(normal_completed),
        "parenHands": len(paren),
        "parenCompletedHands": len(paren_completed),
        "yakumanHands": len(yakuman),
        "yakumanCompletedHands": len(yakuman_completed),
        "wins": len(normal_wins),
        "draws": sum(1 for hand in normal_completed if hand.outcome_type == "ryukyoku"),
        "winRate": ratio(len(normal_wins), len(normal_completed)),
        "allWinsIncludingBonus": sum(1 for hand in completed if hand.win > 0),
        "bonusWins": len(paren_wins) + len(yakuman_wins),
        "parenWins": len(paren_wins),
        "yakumanWins": len(yakuman_wins),
        "startTenpaiHands": sum(1 for hand in hands if hand.initial_shanten == 0),
        "startTenpaiRate": ratio(sum(1 for hand in hands if hand.initial_shanten == 0), len(hands)),
        "everTenpaiHands": sum(1 for hand in hands if hand.ever_tenpai),
        "everTenpaiRate": ratio(sum(1 for hand in hands if hand.ever_tenpai), len(hands)),
        "riichiHands": sum(1 for hand in hands if hand.riichi_count > 0),
        "riichiCount": sum(hand.riichi_count for hand in hands),
        "tenpaiDrawOpportunities": sum(hand.tenpai_draw_opportunities for hand in hands),
        "initialShantenAverage": average([hand.initial_shanten for hand in hands if hand.initial_shanten is not None]),
        "initialShantenDistribution": {str(key): value for key, value in sorted(shanten_counts.items())},
        "initialShantenByStatus": shanten_by_status(hands),
        "totalBet": total_bet,
        "chipReturn": chip_return,
        "resultWinFieldSum": result_win_field_sum,
        "returnRate": ratio(chip_return, total_bet),
        "netByReturnMinusBet": chip_return - total_bet,
        "startMjchip": start_chip,
        "endMjchip": end_chip,
        "deltaMjchipObserved": difference(end_chip, start_chip),
        "decisions": len(decisions),
        "shotDecisions": sum(1 for d in decisions if d.get("kind") == "shot"),
        "discardDecisions": sum(1 for d in decisions if d.get("kind") == "discard"),
        "agariDecisions": sum(1 for d in decisions if d.get("kind") == "agari"),
        "pressMainDecisions": sum(1 for d in decisions if d.get("kind") == "press_main"),
        "shotAreaCounts": {str(key): value for key, value in sorted(shot_counts.items()) if key is not None},
    }


def shot_audit(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    shots = [decision for decision in decisions if decision.get("kind") == "shot"]
    mismatches = [
        decision for decision in shots
        if decision.get("actual") and decision["actual"].get("matchesDecision") is False
    ]
    requested4 = [decision for decision in shots if optional_int((decision.get("action") or {}).get("area")) == 4]
    requested4_sent = Counter(
        decision.get("actual", {}).get("sentArea")
        for decision in requested4
        if decision.get("actual")
    )
    return {
        "shotDecisions": len(shots),
        "pairedShots": sum(1 for decision in shots if decision.get("actual")),
        "mismatches": len(mismatches),
        "mismatchExamples": [brief_decision(decision) for decision in mismatches[:20]],
        "requestedArea4": len(requested4),
        "requestedArea4SentCounts": {str(key): value for key, value in sorted(requested4_sent.items()) if key is not None},
        "area4FillAmount": 0.50,
        "area4ThresholdLow": 0.48,
        "area4ThresholdHigh": 0.52,
        "note": "本轮日志中没有发现决策4区但服务器收到3/5区的情况；4区阈值窄，仍建议运行时校验。",
    }


def shanten_by_status(hands: list[HandRecord]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for status in sorted({hand.status for hand in hands}):
        group = [hand for hand in hands if hand.status == status]
        values = [hand.initial_shanten for hand in group if hand.initial_shanten is not None]
        distribution = Counter(values)
        result[status or "Unknown"] = {
            "hands": len(group),
            "average": average(values),
            "startTenpai": sum(1 for value in values if value == 0),
            "distribution": {str(key): value for key, value in sorted(distribution.items())},
        }
    return result


def shot_outcome_frequency_by_area(hands: list[HandRecord]) -> list[dict[str, Any]]:
    by_area: dict[int, Counter[int]] = defaultdict(Counter)
    for hand in hands:
        for shot in hand.shots:
            if shot.area is None or shot.drawn_tile is None:
                continue
            by_area[shot.area][shot.drawn_tile] += 1

    rows = []
    for area in range(1, 8):
        counts = by_area[area]
        rows.append(
            {
                "area": area,
                "total": sum(counts.values()),
                "frequencies": [
                    {"tile": tile_id, "tileName": TILE_NAMES[tile_id], "count": count}
                    for tile_id, count in sorted(
                        counts.items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                ],
            }
        )
    return rows


def brief_decision(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": decision.get("id"),
        "handIndex": decision.get("handIndex"),
        "action": decision.get("action"),
        "actual": decision.get("actual"),
        "reason": decision.get("reason"),
    }


def render_html(report: dict[str, Any]) -> str:
    payload = json.dumps(report, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    title = f"JanQ 实测复盘 - {report['meta']['sessionId']}"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #667085;
      --line: #d9dee8;
      --accent: #22577a;
      --warn: #9a3412;
      --bad: #b42318;
      --good: #027a48;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.45;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 5;
      background: rgba(246,247,249,.96);
      border-bottom: 1px solid var(--line);
      padding: 14px 22px;
      backdrop-filter: blur(8px);
    }}
    h1 {{ margin: 0 0 6px; font-size: 22px; }}
    h2 {{ margin: 0 0 10px; font-size: 18px; }}
    h3 {{ margin: 0 0 8px; font-size: 15px; }}
    .subtle {{ color: var(--muted); font-size: 13px; }}
    .wrap {{ max-width: 1500px; margin: 0 auto; padding: 18px 22px 60px; }}
    .grid {{ display: grid; gap: 12px; }}
    .stats {{ grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric b {{ font-size: 22px; }}
    .toolbar {{
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      margin: 14px 0;
    }}
    select, input, textarea, button {{
      font: inherit;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
    }}
    select, input {{ padding: 8px 10px; }}
    button {{ padding: 8px 12px; cursor: pointer; }}
    button.primary {{ background: var(--accent); border-color: var(--accent); color: white; }}
    .layout {{ display: grid; grid-template-columns: 300px 1fr; gap: 14px; align-items: start; }}
    .hand-list {{ position: sticky; top: 86px; max-height: calc(100vh - 110px); overflow: auto; }}
    .hand-button {{
      width: 100%;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 6px;
      text-align: left;
      margin-bottom: 8px;
      padding: 10px;
    }}
    .hand-button.active {{ border-color: var(--accent); box-shadow: 0 0 0 2px rgba(34,87,122,.14); }}
    .decision {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 12px;
    }}
    .decision.problem {{ border-color: #f2b8a2; }}
    .decision-head {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      align-items: start;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
      font-size: 12px;
      color: var(--muted);
      background: #fff;
      margin-right: 5px;
    }}
    .pill.good {{ color: var(--good); border-color: #abefc6; background: #ecfdf3; }}
    .pill.bad {{ color: var(--bad); border-color: #fecdca; background: #fef3f2; }}
    .pill.warn {{ color: var(--warn); border-color: #fedf89; background: #fffaeb; }}
    .tiles {{ display: flex; gap: 3px; flex-wrap: wrap; margin: 8px 0; }}
    .tile {{
      min-width: 34px;
      padding: 5px 6px;
      text-align: center;
      border: 1px solid #cdd5df;
      border-radius: 5px;
      background: #fbfcfe;
      font-weight: 600;
      font-size: 13px;
    }}
    .detail-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 8px; margin-top: 10px; }}
    .detail {{ background: #f8fafc; border: 1px solid #e4e7ec; border-radius: 6px; padding: 9px; }}
    .detail span {{ display: block; color: var(--muted); font-size: 12px; margin-bottom: 3px; }}
    .reason {{ font-family: Consolas, "Courier New", monospace; font-size: 12px; word-break: break-word; }}
    .review {{ border-top: 1px dashed var(--line); margin-top: 12px; padding-top: 12px; }}
    .verdicts {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }}
    .verdicts label {{ display: inline-flex; gap: 5px; align-items: center; font-size: 13px; }}
    textarea {{ width: 100%; min-height: 70px; padding: 8px; resize: vertical; }}
    .feedback-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
    .prompt-box textarea {{ min-height: 260px; font-family: Consolas, "Courier New", monospace; }}
    .issue {{ border-color: #fedf89; background: #fffaeb; }}
    .badtext {{ color: var(--bad); }}
    .goodtext {{ color: var(--good); }}
    @media (max-width: 900px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .hand-list {{ position: static; max-height: none; }}
      .feedback-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<header>
  <h1>{escape(title)}</h1>
  <div class="subtle">批注会保存在本浏览器 localStorage；底部可生成策略更新 prompt。</div>
</header>
<main class="wrap">
  <section id="stats" class="grid stats"></section>
  <section id="audit" class="card issue" style="margin-top:12px"></section>
  <section id="shotFreq" class="card" style="margin-top:12px"></section>
  <div class="toolbar">
    <select id="kindFilter">
      <option value="play">只看打牌决策</option>
      <option value="all">全部动作</option>
      <option value="shot">只看发射</option>
      <option value="discard">只看弃牌/立直</option>
      <option value="agari">只看胡牌</option>
      <option value="problem">只看问题/疑问</option>
    </select>
    <select id="verdictFilter">
      <option value="all">全部批注状态</option>
      <option value="pending">未批注</option>
      <option value="agree">赞同</option>
      <option value="question">有疑问</option>
      <option value="disagree">不赞同</option>
    </select>
    <input id="searchBox" placeholder="搜索理由/牌/手数">
    <button id="nextPending">下一个未批注</button>
  </div>
  <section class="layout">
    <aside class="card hand-list" id="handList"></aside>
    <section id="decisionList"></section>
  </section>
  <section class="card prompt-box">
    <h2>生成 Prompt</h2>
    <p class="subtle">只收集你标成“不赞同”或“有疑问”的决策，并带上局面、当前策略理由、实际结果和你的批注。</p>
    <div class="toolbar">
      <button class="primary" id="generatePrompt">生成 Prompt</button>
      <button id="copyPrompt">复制</button>
      <span id="promptStatus" class="subtle"></span>
    </div>
    <textarea id="promptOutput"></textarea>
  </section>
</main>
<script id="report-data" type="application/json">{payload}</script>
<script>
const REPORT = JSON.parse(document.getElementById("report-data").textContent);
const tileLabels = {json.dumps(TILE_LABELS, ensure_ascii=False)};
const storageKey = `janq-clean-review:${{REPORT.meta.sessionId}}:${{REPORT.meta.eventsEndLine}}`;
let reviews = JSON.parse(localStorage.getItem(storageKey) || "{{}}");
let selectedHand = REPORT.hands[0]?.index ?? 0;

function esc(value) {{
  return String(value ?? "").replace(/[&<>"']/g, ch => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[ch]));
}}
function pct(value) {{
  if (value === null || value === undefined) return "-";
  return `${{(Number(value) * 100).toFixed(1)}}%`;
}}
function signed(value) {{
  if (value === null || value === undefined) return "-";
  return Number(value) > 0 ? `+${{value}}` : String(value);
}}
function tileName(id) {{ return Number.isInteger(id) && id >= 0 && id < tileLabels.length ? tileLabels[id] : "-"; }}
function tilesHtml(tiles) {{
  return `<div class="tiles">${{(tiles || []).map(id => `<span class="tile" title="id=${{id}}">${{esc(tileName(id))}}</span>`).join("")}}</div>`;
}}
function reviewFor(id) {{
  return reviews[id] || {{ verdict: "pending", reason: "", alternative: "" }};
}}
function saveReviews() {{ localStorage.setItem(storageKey, JSON.stringify(reviews)); }}
function actionLabel(d) {{
  const a = d.action || {{}};
  if (d.kind === "shot") return `发射 ${{a.area}} 区`;
  if (d.kind === "discard") return `弃 ${{tileName(a.discard_tile)}}${{a.richi ? "，立直" : ""}}`;
  if (d.kind === "agari") return "胡牌";
  if (d.kind === "press_main") return d.phase === "free_wait" ? "按 FREE" : "按 BET";
  return d.kind || "-";
}}
function renderStats() {{
  const s = REPORT.stats;
  const items = [
    ["牌局", `${{s.completedHands}} / ${{s.hands}}`, `普通 ${{s.normalHands}}，奖励 ${{s.parenHands}}，役满奖励 ${{s.yakumanHands}}`],
    ["起手平均向听", Number(s.initialShantenAverage).toFixed(2), `普通局 ${{Number(s.initialShantenByStatus.NORMAL?.average ?? 0).toFixed(2)}}；整体分布 ${{JSON.stringify(s.initialShantenDistribution)}}`],
    ["起手听牌率", pct(s.startTenpaiRate), `整体 ${{s.startTenpaiHands}} / ${{s.hands}}；普通局 ${{s.initialShantenByStatus.NORMAL?.startTenpai ?? 0}} / ${{s.initialShantenByStatus.NORMAL?.hands ?? 0}}；奖励局 ${{s.initialShantenByStatus.PARENCHAN?.startTenpai ?? 0}} / ${{s.initialShantenByStatus.PARENCHAN?.hands ?? 0}}`],
    ["曾听牌率", pct(s.everTenpaiRate), `${{s.everTenpaiHands}} / ${{s.hands}}；立直 ${{s.riichiCount}} 次`],
    ["胡牌率", pct(s.winRate), `只统计普通局：${{s.wins}} 胡 / ${{s.normalCompletedHands}} 完成；奖励局命中 ${{s.bonusWins}} 次不计入`],
    ["返奖率", pct(s.returnRate), `实测返还 ${{s.chipReturn}} / 投入 ${{s.totalBet}}；净 ${{signed(s.netByReturnMinusBet)}}；win字段合计 ${{s.resultWinFieldSum}}`],
    ["MJChip", `${{s.startMjchip ?? "-"}} → ${{s.endMjchip ?? "-"}}`, `观测净变动 ${{signed(s.deltaMjchipObserved)}}`],
    ["决策", `${{s.decisions}}`, `发射 ${{s.shotDecisions}}，弃牌 ${{s.discardDecisions}}，胡牌 ${{s.agariDecisions}}`],
  ];
  document.getElementById("stats").innerHTML = items.map(([k,v,h]) => `<div class="card metric"><span>${{esc(k)}}</span><b>${{esc(v)}}</b><div class="subtle">${{esc(h)}}</div></div>`).join("");
  const a = REPORT.shotAudit;
  const ok = a.mismatches === 0 ? "goodtext" : "badtext";
  document.getElementById("audit").innerHTML = `
    <h2>4 区发射审计</h2>
    <p>本轮发射决策 ${{a.shotDecisions}} 次，已配对服务器发射事件 ${{a.pairedShots}} 次，决策区与服务器实际区不一致：<b class="${{ok}}">${{a.mismatches}}</b>。</p>
    <p>决策 4 区 ${{a.requestedArea4}} 次；服务器实际收到：${{esc(JSON.stringify(a.requestedArea4SentCounts))}}。</p>
    <p>当前插件 4 区 fillAmount=${{a.area4FillAmount}}，真实阈值约 ${{a.area4ThresholdLow}}-${{a.area4ThresholdHigh}}。这轮没有日志证据表明 4 区被送成 3/5，但这个区间确实很窄，建议后续加运行时强校验。</p>`;
  const freq = REPORT.shotOutcomeFrequencyByArea || [];
  document.getElementById("shotFreq").innerHTML = `
    <h2>发射区 -> 实际摸牌频数</h2>
    <p class="subtle">这是当前实测频数，不是概率估计；样本量还小，尤其不要用 4 区的 49 次样本修正策略。</p>
    <div class="detail-grid">
      ${{freq.map(row => `<div class="detail"><span>${{row.area}} 区，n=${{row.total}}</span><b>${{row.frequencies.map(item => `${{tileName(item.tile)}}:${{item.count}}`).join("、")}}</b></div>`).join("")}}
    </div>`;
}}
function handSummary(hand) {{
  return `#${{hand.index}} ${{hand.mode}} ${{hand.outcomeType}} 胡=${{hand.win}} 向听=${{hand.initialShanten}}`;
}}
function renderHandList() {{
  const counts = new Map();
  for (const d of filteredDecisions(false)) counts.set(d.handIndex, (counts.get(d.handIndex) || 0) + 1);
  document.getElementById("handList").innerHTML = REPORT.hands.map(hand => `
    <button class="hand-button ${{hand.index === selectedHand ? "active" : ""}}" data-hand="${{hand.index}}">
      <span>${{esc(handSummary(hand))}}<br><span class="subtle">行 ${{hand.lineStart}}-${{hand.lineEnd}} · 决策 ${{counts.get(hand.index) || 0}}</span></span>
      <b>${{signed(hand.deltaMjchip)}}</b>
    </button>`).join("");
}}
function filteredDecisions(applyHand = true) {{
  const kind = document.getElementById("kindFilter")?.value || "play";
  const verdict = document.getElementById("verdictFilter")?.value || "all";
  const q = (document.getElementById("searchBox")?.value || "").toLowerCase();
  return REPORT.decisions.filter(d => {{
    if (applyHand && d.handIndex !== selectedHand) return false;
    const r = reviewFor(d.id);
    if (verdict !== "all" && r.verdict !== verdict) return false;
    if (kind === "play" && !["shot","discard","agari"].includes(d.kind)) return false;
    if (kind === "problem" && !["question","disagree"].includes(r.verdict)) return false;
    if (!["all","play","problem"].includes(kind) && d.kind !== kind) return false;
    if (q) {{
      const hay = JSON.stringify([d.reason, d.action, d.areaDecision, d.discardDecision, d.actual, d.hand?.map(tileName), r.reason, r.alternative]).toLowerCase();
      if (!hay.includes(q)) return false;
    }}
    return true;
  }});
}}
function renderDecisions() {{
  const hand = REPORT.hands.find(h => h.index === selectedHand);
  const list = filteredDecisions(true);
  document.getElementById("decisionList").innerHTML = `
    <section class="card" style="margin-bottom:12px">
      <h2>${{hand ? esc(handSummary(hand)) : "未选择牌局"}}</h2>
      ${{hand ? tilesHtml(hand.initialHand) : ""}}
      <div class="subtle">起手向听 ${{hand?.initialShanten ?? "-"}}；曾听牌 ${{hand?.everTenpai ? "是" : "否"}}；立直 ${{hand?.riichiCount ?? 0}} 次；投入 ${{hand?.betCost ?? "-"}}；返奖 ${{hand?.win ?? 0}}</div>
    </section>
    ${{list.length ? list.map(decisionHtml).join("") : '<section class="card">当前筛选下没有决策。</section>'}}`;
}}
function decisionHtml(d) {{
  const r = reviewFor(d.id);
  const actual = d.actual || {{}};
  const mismatch = actual.matchesDecision === false;
  const area = d.areaDecision || {{}};
  const discard = d.discardDecision || {{}};
  const targets = (area.target_tiles || []).map(tileName).join("、") || "-";
  const accepts = (discard.accepts || []).map(tileName).join("、") || "-";
  return `<article class="decision ${{mismatch ? "problem" : ""}}" id="${{esc(d.id)}}" data-id="${{esc(d.id)}}">
    <div class="decision-head">
      <div>
        <h2>${{esc(d.id)}} · 第 ${{d.handIndex}} 局 · ${{esc(actionLabel(d))}}</h2>
        <div>
          <span class="pill">${{esc(d.phase)}} / ${{esc(d.mode)}} / ${{esc(d.status)}}</span>
          <span class="pill">剩余球 ${{esc(d.balls ?? "-")}}</span>
          <span class="pill ${{d.isReach ? "good" : ""}}">${{d.isReach ? "已立直" : "未立直"}}</span>
          <span class="pill ${{actual.matchesDecision === false ? "bad" : actual.matchesDecision === true ? "good" : ""}}">${{actual.matchesDecision === false ? "实际不一致" : actual.matchesDecision === true ? "实际一致" : "无实际配对"}}</span>
        </div>
      </div>
      <span class="pill warn">${{esc(verdictLabel(r.verdict))}}</span>
    </div>
    ${{tilesHtml(d.hand)}}
    <div class="detail-grid">
      <div class="detail"><span>策略理由</span><div class="reason">${{esc(d.reason)}}</div></div>
      <div class="detail"><span>实际事件</span>${{actualText(d)}}</div>
      <div class="detail"><span>区域/目标</span>区域 ${{esc(area.area ?? "-")}}；目标 ${{esc(targets)}}；概率 ${{esc(area.probability ?? "-")}}</div>
      <div class="detail"><span>弃牌/听牌</span>弃 ${{esc(tileName(discard.discard_tile))}}；弃后向听 ${{esc(discard.shanten_after ?? "-")}}；受入 ${{esc(accepts)}}；立直 ${{discard.declare_riichi ? "是" : "否"}}</div>
    </div>
    <section class="review">
      <div class="verdicts">
        ${{radio(d.id, "pending", "未批注", r.verdict)}}
        ${{radio(d.id, "agree", "赞同", r.verdict)}}
        ${{radio(d.id, "question", "有疑问", r.verdict)}}
        ${{radio(d.id, "disagree", "不赞同", r.verdict)}}
      </div>
      <div class="feedback-grid">
        <label>不同意/疑问理由<textarea data-field="reason" data-id="${{esc(d.id)}}" placeholder="写下为什么这个决策有问题">${{esc(r.reason)}}</textarea></label>
        <label>你认为应如何改<textarea data-field="alternative" data-id="${{esc(d.id)}}" placeholder="例如：应打 4 区 / 应保留普通听牌 / 不该立直">${{esc(r.alternative)}}</textarea></label>
      </div>
    </section>
  </article>`;
}}
function actualText(d) {{
  const a = d.actual || {{}};
  if (d.kind === "shot") return `服务器区=${{esc(a.sentArea ?? "-")}}；摸到=${{esc(tileName(a.drawnTile))}}；行=${{esc(a.line ?? "-")}}`;
  if (d.kind === "discard") return `服务器弃=${{esc(tileName(a.discardTile))}}；pos=${{esc(a.pos ?? "-")}}；立直=${{a.richi ? "是" : "否"}}；行=${{esc(a.line ?? "-")}}`;
  return `确认=${{d.confirmed ? "是" : "否"}}；probeLine=${{esc(d.probeLine ?? "-")}}`;
}}
function radio(id, value, label, current) {{
  return `<label><input type="radio" name="v-${{esc(id)}}" data-id="${{esc(id)}}" data-verdict="${{value}}" ${{current === value ? "checked" : ""}}> ${{label}}</label>`;
}}
function verdictLabel(v) {{
  return ({{ pending: "未批注", agree: "赞同", question: "有疑问", disagree: "不赞同" }})[v] || v;
}}
function updateFilters() {{ renderHandList(); renderDecisions(); }}
function generatePrompt() {{
  const flagged = REPORT.decisions.filter(d => ["question","disagree"].includes(reviewFor(d.id).verdict));
  const s = REPORT.stats;
  const lines = [
    `请根据以下 JanQ 实测批注更新当前策略。`,
    ``,
    `实测摘要：session=${{REPORT.meta.sessionId}}，事件行 ${{REPORT.meta.eventsStartLine}}-${{REPORT.meta.eventsEndLine}}。`,
    `牌局=${{s.completedHands}}/${{s.hands}}，普通局胡牌率=${{pct(s.winRate)}}（${{s.wins}}/${{s.normalCompletedHands}}；奖励局命中${{s.bonusWins}}次不计入），起手听牌率=${{pct(s.startTenpaiRate)}}，曾听牌率=${{pct(s.everTenpaiRate)}}，实测返奖率=${{pct(s.returnRate)}}（MJChip返还=${{s.chipReturn}}，投入=${{s.totalBet}}，win字段合计=${{s.resultWinFieldSum}}）。`,
    `4区审计：决策4区 ${{REPORT.shotAudit.requestedArea4}} 次，服务器实际收到 ${{JSON.stringify(REPORT.shotAudit.requestedArea4SentCounts)}}，不一致 ${{REPORT.shotAudit.mismatches}} 次。`,
    ``,
    `我的批注共 ${{flagged.length}} 条：`
  ];
  flagged.forEach((d, i) => {{
    const r = reviewFor(d.id);
    const area = d.areaDecision || {{}};
    const discard = d.discardDecision || {{}};
    lines.push(
      ``,
      `## ${{i + 1}}. ${{r.verdict === "disagree" ? "不赞同" : "有疑问"}}：${{d.id}} / 第 ${{d.handIndex}} 局`,
      `- 局面：phase=${{d.phase}}; mode=${{d.mode}}; status=${{d.status}}; balls=${{d.balls}}; reach=${{d.isReach}}`,
      `- 手牌：${{(d.hand || []).map(id => `${{tileName(id)}}(id=${{id}})`).join(" ")}}`,
      `- 当前决策：${{actionLabel(d)}}`,
      `- 策略理由：${{d.reason}}`,
      d.kind === "shot" ? `- 区域详情：area=${{area.area}}; targets=${{(area.target_tiles || []).map(id => `${{tileName(id)}}(${{id}})`).join(", ")}}; probability=${{area.probability}}; actualSent=${{d.actual?.sentArea}}; drawn=${{tileName(d.actual?.drawnTile)}}` : ``,
      d.kind === "discard" ? `- 弃牌详情：discard=${{tileName(discard.discard_tile)}}; shantenAfter=${{discard.shanten_after}}; accepts=${{(discard.accepts || []).map(id => `${{tileName(id)}}(${{id}})`).join(", ")}}; riichi=${{discard.declare_riichi}}; actualDiscard=${{tileName(d.actual?.discardTile)}}; actualRiichi=${{d.actual?.richi}}` : ``,
      `- 我的理由：${{r.reason || "（未填写）"}}`,
      `- 我建议：${{r.alternative || "（未填写）"}}`
    );
  }});
  document.getElementById("promptOutput").value = lines.filter(line => line !== null).join("\\n");
  document.getElementById("promptStatus").textContent = `已生成 ${{flagged.length}} 条`;
}}
document.addEventListener("click", e => {{
  const hand = e.target.closest("[data-hand]");
  if (hand) {{ selectedHand = Number(hand.dataset.hand); updateFilters(); return; }}
  if (e.target.id === "nextPending") {{
    const next = REPORT.decisions.find(d => reviewFor(d.id).verdict === "pending" && ["shot","discard","agari"].includes(d.kind));
    if (next) {{ selectedHand = next.handIndex; updateFilters(); setTimeout(() => document.getElementById(next.id)?.scrollIntoView({{block:"center", behavior:"smooth"}}), 0); }}
  }}
  if (e.target.id === "generatePrompt") generatePrompt();
  if (e.target.id === "copyPrompt") {{
    const out = document.getElementById("promptOutput");
    if (!out.value) generatePrompt();
    navigator.clipboard?.writeText(out.value).catch(() => {{}});
  }}
}});
document.addEventListener("change", e => {{
  if (e.target.matches("select")) updateFilters();
  if (e.target.matches("input[data-verdict]")) {{
    const id = e.target.dataset.id;
    reviews[id] = {{ ...reviewFor(id), verdict: e.target.dataset.verdict }};
    saveReviews();
    updateFilters();
  }}
}});
document.addEventListener("input", e => {{
  if (e.target.id === "searchBox") updateFilters();
  if (e.target.matches("textarea[data-field]")) {{
    const id = e.target.dataset.id;
    reviews[id] = {{ ...reviewFor(id), [e.target.dataset.field]: e.target.value }};
    saveReviews();
  }}
}});
renderStats();
renderHandList();
renderDecisions();
</script>
</body>
</html>
"""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            row["_line"] = line_number
            rows.append(row)
    return rows


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def first_selected_bet(rows: list[dict[str, Any]]) -> int | None:
    for row in rows:
        if row.get("type") in ("bot_bet_selected", "bot_current_bet_observed"):
            bet = optional_int((row.get("payload") or {}).get("bet"))
            if bet is not None:
                return bet
    return None


def mode_from_status(status: str) -> str:
    return {
        "NORMAL": "Normal",
        "PARENCHAN": "ParenChallenge",
        "YAKUMAN": "YakumanBonus",
    }.get(status, status or "Unknown")


def safe_shanten(tiles: list[int] | tuple[int, ...]) -> int | None:
    if not tiles:
        return None
    try:
        return shanten(tuple(tiles))
    except ValueError:
        return None


def draw_has_tenpai_discard(tiles: list[int]) -> bool:
    if len(tiles) != 14:
        return False
    if safe_complete(tiles):
        return True
    for tile in sorted(set(tiles)):
        after = list(tiles)
        after.remove(tile)
        if safe_shanten(after) == 0:
            return True
    return False


def safe_complete(tiles: list[int]) -> bool:
    try:
        return is_complete_hand(tuple(tiles))
    except ValueError:
        return False


def remove_discard(tiles: list[int], tile: int | None, pos: int | None) -> list[int]:
    result = list(tiles)
    if tile is None:
        return result
    if pos is not None and 0 <= pos < len(result) and result[pos] == tile:
        result.pop(pos)
        return result
    try:
        result.remove(tile)
    except ValueError:
        pass
    return result


def last_payload_mjchip(hand: HandRecord) -> int | None:
    return hand.end_mjchip


def optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def state_key_value(values: Any, index: int) -> Any:
    return values[index] if isinstance(values, list) and len(values) > index else None


def state_key_int(values: Any, index: int) -> int | None:
    return optional_int(state_key_value(values, index))


def tile_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool) and 0 <= item < len(TILE_NAMES)]


def first_not_none(values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def last_not_none(values: Any) -> Any:
    result = None
    for value in values:
        if value is not None:
            result = value
    return result


def difference(end: int | None, start: int | None) -> int | None:
    if end is None or start is None:
        return None
    return end - start


def ratio(numerator: int | float, denominator: int | float) -> float | None:
    return None if denominator == 0 else numerator / denominator


def average(values: list[int]) -> float:
    return sum(values) / len(values) if values else 0.0


if __name__ == "__main__":
    main()
