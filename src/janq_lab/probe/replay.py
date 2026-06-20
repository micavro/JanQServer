"""Rebuild coarse JanQ hands from JanqProbe events."""

from __future__ import annotations

from dataclasses import dataclass, field
import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from janq_lab.probe.events import ProbeEvent, count_by_type, read_events
from janq_lab.probe.normalize import (
    normalize_haipai_payload,
    normalize_result_payload,
    normalize_tsumo_payload,
)


@dataclass(frozen=True)
class CurrencySnapshot:
    gold: int | None = None
    mjchip: int | None = None
    cchip: int | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CurrencySnapshot":
        return cls(
            gold=_optional_int(payload.get("gold")),
            mjchip=_optional_int(payload.get("mjchip")),
            cchip=_optional_int(payload.get("cchip")),
        )

    def delta(self, other: "CurrencySnapshot") -> dict[str, int | None]:
        return {
            "gold": _delta(self.gold, other.gold),
            "mjchip": _delta(self.mjchip, other.mjchip),
            "cchip": _delta(self.cchip, other.cchip),
        }


@dataclass(frozen=True)
class ObservedShot:
    area: int | None
    pai: int | None
    pai_model: int | None
    zandan: int | None
    replay: bool | None
    tehai: tuple[int, ...]
    tehai_model: tuple[int, ...]
    event_line: int


@dataclass(frozen=True)
class ObservedDahai:
    richi: bool | None
    pos: int | None
    sutehai: int | None
    event_line: int


@dataclass
class ObservedHand:
    index: int
    start_line: int
    status: str | None
    zandan_start: int | None
    haipai: tuple[int, ...]
    haipai_model: tuple[int, ...]
    dora_model: int | None
    ura_dora_model: int | None
    start_currency: CurrencySnapshot
    shots: list[ObservedShot] = field(default_factory=list)
    dahai: list[ObservedDahai] = field(default_factory=list)
    result_line: int | None = None
    result_payload: dict[str, Any] | None = None
    end_currency: CurrencySnapshot | None = None

    @property
    def complete(self) -> bool:
        return self.result_payload is not None

    @property
    def win(self) -> int | None:
        if self.result_payload is None:
            return None
        return _optional_int(self.result_payload.get("win"))

    @property
    def currency_delta(self) -> dict[str, int | None] | None:
        if self.end_currency is None:
            return None
        return self.start_currency.delta(self.end_currency)

    def to_summary(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "complete": self.complete,
            "start_line": self.start_line,
            "result_line": self.result_line,
            "status": self.status,
            "zandan_start": self.zandan_start,
            "haipai_len": len(self.haipai),
            "haipai_model_len": len(self.haipai_model),
            "shots": len(self.shots),
            "dahai": len(self.dahai),
            "win": self.win,
            "han": None if self.result_payload is None else self.result_payload.get("han"),
            "yakuLevel": None if self.result_payload is None else self.result_payload.get("yakuLevel"),
            "odds": None if self.result_payload is None else self.result_payload.get("odds"),
            "currency_delta": self.currency_delta,
        }


@dataclass(frozen=True)
class ReplaySummary:
    event_counts: dict[str, int]
    hands: tuple[ObservedHand, ...]

    @property
    def complete_hands(self) -> tuple[ObservedHand, ...]:
        return tuple(hand for hand in self.hands if hand.complete)

    def to_dict(self) -> dict[str, Any]:
        complete = self.complete_hands
        wins = [hand.win for hand in complete if hand.win is not None]
        return {
            "event_counts": self.event_counts,
            "hands": len(self.hands),
            "complete_hands": len(complete),
            "wins": sum(1 for win in wins if win),
            "hand_summaries": [hand.to_summary() for hand in self.hands],
        }


def replay_events(events: Iterable[ProbeEvent]) -> ReplaySummary:
    event_list = list(events)
    hands: list[ObservedHand] = []
    current: ObservedHand | None = None
    pending_area: int | None = None

    for event in event_list:
        payload = event.payload
        if event.type == "recv_game_haipai":
            normalized = normalize_haipai_payload(payload)
            current = ObservedHand(
                index=len(hands) + 1,
                start_line=event.line_number,
                status=_optional_str(payload.get("status")),
                zandan_start=_optional_int(payload.get("zandan")),
                haipai=tuple(_int_list(payload.get("haipai"))),
                haipai_model=normalized["haipai"],
                dora_model=normalized["dora"],
                ura_dora_model=normalized["ura_dora"],
                start_currency=CurrencySnapshot.from_payload(payload),
            )
            hands.append(current)
            pending_area = None
        elif event.type == "send_action_shot":
            pending_area = _optional_int(payload.get("area"))
        elif event.type == "recv_game_tsumo" and current is not None:
            normalized = normalize_tsumo_payload(payload)
            current.shots.append(
                ObservedShot(
                    area=pending_area,
                    pai=_optional_int(payload.get("pai")),
                    pai_model=normalized["pai"],
                    zandan=_optional_int(payload.get("zandan")),
                    replay=_optional_bool(payload.get("replay")),
                    tehai=tuple(_int_list(payload.get("tehai"))),
                    tehai_model=normalized["tehai"],
                    event_line=event.line_number,
                )
            )
            pending_area = None
        elif event.type == "recv_act_dahai" and current is not None:
            current.dahai.append(
                ObservedDahai(
                    richi=_optional_bool(payload.get("richi")),
                    pos=_optional_int(payload.get("pos")),
                    sutehai=_optional_int(payload.get("sutehai")),
                    event_line=event.line_number,
                )
            )
        elif event.type == "recv_janq_result" and current is not None:
            current.result_line = event.line_number
            current.result_payload = dict(payload)
            current.result_payload["tehai_model"] = normalize_result_payload(payload)["tehai"]
            current.end_currency = CurrencySnapshot.from_payload(payload)
            current = None
            pending_area = None

    return ReplaySummary(event_counts=count_by_type(event_list), hands=tuple(hands))


def replay_file(path: str | Path) -> ReplaySummary:
    return replay_events(read_events(path))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Summarize JanqProbe JSONL events.")
    parser.add_argument("path", help="Path to janq_events.jsonl")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args(argv)

    summary = replay_file(args.path)
    if args.json:
        print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        return

    data = summary.to_dict()
    print(f"events: {data['event_counts']}")
    print(f"hands: {data['hands']} complete={data['complete_hands']} wins={data['wins']}")
    for hand in data["hand_summaries"]:
        print(
            "hand {index}: complete={complete} shots={shots} win={win} "
            "han={han} odds={odds} delta={currency_delta}".format(**hand)
        )


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool)]


def _delta(start: int | None, end: int | None) -> int | None:
    if start is None or end is None:
        return None
    return end - start


if __name__ == "__main__":
    main()
