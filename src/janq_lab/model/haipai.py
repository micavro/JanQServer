"""Normal-game initial hand sources for JanQ simulations."""

from __future__ import annotations

from dataclasses import dataclass
import random
from pathlib import Path
from typing import Iterable

from janq_lab.model.hand import TileSet, tile_set
from janq_lab.probe.events import ProbeEvent, read_events
from janq_lab.probe.replay import ObservedHand, replay_events


WALL = tuple(tile_id for tile_id in range(34) for _ in range(4))


def random_wall_hand(rng: random.Random | None = None) -> TileSet:
    """Sample a 13-tile hand from a physical 136-tile wall."""

    source = rng if rng is not None else random.Random()
    return tile_set(source.sample(WALL, 13))


@dataclass(frozen=True)
class ObservedHaipaiSet:
    hands: tuple[TileSet, ...]
    source: str
    ignored_hands: int = 0

    def sample(self, rng: random.Random | None = None) -> TileSet:
        if not self.hands:
            raise ValueError("observed haipai set is empty")
        source = rng if rng is not None else random.Random()
        return source.choice(self.hands)


def load_observed_normal_haipai(path: str | Path) -> ObservedHaipaiSet:
    events = tuple(read_events(path, skip_bad_lines=True))
    return observed_normal_haipai(events, source=str(path))


def observed_normal_haipai(
    events: Iterable[ProbeEvent],
    *,
    source: str = "<events>",
) -> ObservedHaipaiSet:
    summary = replay_events(events)
    hands: list[TileSet] = []
    ignored = 0

    for hand in summary.hands:
        if not _is_normal_13_tile_haipai(hand):
            ignored += 1
            continue
        hands.append(tile_set(hand.haipai_model))

    return ObservedHaipaiSet(hands=tuple(hands), source=source, ignored_hands=ignored)


def _is_normal_13_tile_haipai(hand: ObservedHand) -> bool:
    status = (hand.status or "").upper()
    if status and status != "NORMAL":
        return False
    return len(hand.haipai_model) == 13
