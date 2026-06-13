"""Tile identifiers used by JanQ and the MJ client."""

from __future__ import annotations

from dataclasses import dataclass


TILE_COUNT = 34

TILE_NAMES: tuple[str, ...] = (
    "1m",
    "2m",
    "3m",
    "4m",
    "5m",
    "6m",
    "7m",
    "8m",
    "9m",
    "1s",
    "2s",
    "3s",
    "4s",
    "5s",
    "6s",
    "7s",
    "8s",
    "9s",
    "1p",
    "2p",
    "3p",
    "4p",
    "5p",
    "6p",
    "7p",
    "8p",
    "9p",
    "E",
    "S",
    "W",
    "N",
    "P",
    "F",
    "C",
)


@dataclass(frozen=True)
class Tile:
    """A JanQ tile id plus its compact display name."""

    id: int
    name: str


def check_tile_id(tile_id: int) -> None:
    if not 0 <= tile_id < TILE_COUNT:
        raise ValueError(f"tile_id must be 0..33, got {tile_id}")


def tile_name(tile_id: int) -> str:
    check_tile_id(tile_id)
    return TILE_NAMES[tile_id]


def all_tiles() -> tuple[Tile, ...]:
    return tuple(Tile(i, name) for i, name in enumerate(TILE_NAMES))

