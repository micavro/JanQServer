"""Normalize raw JanQAPI probe payloads into model-friendly ids.

JanQAPI payloads observed by `JanqProbe` are server/API shaped. `ApiClient`
subtracts 1 before feeding many tile ids to `GameManager`, so this module keeps
the conversion explicit and testable.
"""

from __future__ import annotations

from typing import Any


RAW_TILE_MIN = 1
RAW_TILE_MAX = 34
BLANK_TILE_SENTINEL = 9999


def api_tile_to_model(value: Any) -> int | None:
    """Convert a raw API tile id to the model's 0-based tile id."""

    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if value == BLANK_TILE_SENTINEL or value == 0:
        return None
    if RAW_TILE_MIN <= value <= RAW_TILE_MAX:
        return value - 1
    return None


def api_tiles_to_model(values: Any) -> tuple[int, ...]:
    if not isinstance(values, list):
        return ()
    return tuple(
        normalized
        for value in values
        if (normalized := api_tile_to_model(value)) is not None
    )


def normalize_haipai_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "haipai": api_tiles_to_model(payload.get("haipai")),
        "dora": api_tile_to_model(payload.get("omoDora")),
        "ura_dora": api_tile_to_model(payload.get("uraDora")),
        "tsumo": api_tile_to_model(payload.get("tsumo")),
    }


def normalize_tsumo_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "pai": api_tile_to_model(payload.get("pai")),
        "tehai": api_tiles_to_model(payload.get("tehai")),
        "dora": api_tile_to_model(payload.get("omo_dora")),
        "ura_dora": api_tile_to_model(payload.get("ura_dora")),
    }


def normalize_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "tehai": api_tiles_to_model(payload.get("tehai")),
    }

