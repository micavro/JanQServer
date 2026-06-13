"""Parser for JanQ nyukyu probability tables.

The client stores each nyukyu table as 34 tiles x 7 areas of little-endian
uint16 weights. Values are tile-major in the file:

    raw[tile_id * 7 + area_index]

Each area column should sum to 10000.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import random
import struct
from typing import Iterable

from janq_lab.tiles import TILE_COUNT, tile_name


AREA_COUNT = 7
EXPECTED_WEIGHT_SUM = 10000
NYUKYU_FILENAMES = (
    "nyukyu_base_table.bytes",
    "nyukyu_paren_table.bytes",
    "nyukyu_yakuman_table.bytes",
)


class NyukyuTableError(ValueError):
    """Raised when a JanQ nyukyu table is malformed."""


@dataclass(frozen=True)
class TileWeight:
    tile_id: int
    name: str
    weight: int

    @property
    def probability(self) -> float:
        return self.weight / EXPECTED_WEIGHT_SUM


@dataclass(frozen=True)
class NyukyuTable:
    """A normalized view of one JanQ nyukyu table.

    `areas` is indexed as area-1, then tile id.
    """

    name: str
    source: Path | None
    sha256: str
    areas: tuple[tuple[int, ...], ...]

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        name: str = "<memory>",
        source: Path | None = None,
    ) -> "NyukyuTable":
        expected_values = TILE_COUNT * AREA_COUNT
        expected_bytes = expected_values * 2
        if len(data) != expected_bytes:
            raise NyukyuTableError(
                f"{name} must be {expected_bytes} bytes, got {len(data)}"
            )

        values = struct.unpack("<" + "H" * expected_values, data)
        areas = tuple(
            tuple(values[tile_id * AREA_COUNT + area_idx] for tile_id in range(TILE_COUNT))
            for area_idx in range(AREA_COUNT)
        )
        table = cls(name=name, source=source, sha256=sha256(data).hexdigest(), areas=areas)
        table.validate()
        return table

    @classmethod
    def from_path(cls, path: str | Path) -> "NyukyuTable":
        table_path = Path(path)
        return cls.from_bytes(
            table_path.read_bytes(),
            name=table_path.name,
            source=table_path,
        )

    def validate(self) -> None:
        if len(self.areas) != AREA_COUNT:
            raise NyukyuTableError(f"{self.name} must have {AREA_COUNT} areas")
        for area_idx, weights in enumerate(self.areas, start=1):
            if len(weights) != TILE_COUNT:
                raise NyukyuTableError(
                    f"{self.name} area {area_idx} must have {TILE_COUNT} tile weights"
                )
            total = sum(weights)
            if total != EXPECTED_WEIGHT_SUM:
                raise NyukyuTableError(
                    f"{self.name} area {area_idx} sums to {total}, "
                    f"expected {EXPECTED_WEIGHT_SUM}"
                )

    def weights_for_area(self, area: int) -> tuple[int, ...]:
        check_area(area)
        return self.areas[area - 1]

    def tile_weight(self, area: int, tile_id: int) -> int:
        check_area(area)
        if not 0 <= tile_id < TILE_COUNT:
            raise ValueError(f"tile_id must be 0..33, got {tile_id}")
        return self.areas[area - 1][tile_id]

    def nonzero_weights(self, area: int) -> tuple[TileWeight, ...]:
        check_area(area)
        return tuple(
            TileWeight(tile_id, tile_name(tile_id), weight)
            for tile_id, weight in enumerate(self.areas[area - 1])
            if weight
        )

    def probability(self, area: int, tile_id: int) -> float:
        return self.tile_weight(area, tile_id) / EXPECTED_WEIGHT_SUM

    def draw(self, area: int, rng: random.Random | None = None) -> int:
        check_area(area)
        source = rng if rng is not None else random
        return source.choices(range(TILE_COUNT), weights=self.areas[area - 1], k=1)[0]

    def describe_area(self, area: int) -> str:
        return " ".join(
            f"{entry.name}:{entry.probability:.1%}"
            for entry in self.nonzero_weights(area)
        )


def check_area(area: int) -> None:
    if not 1 <= area <= AREA_COUNT:
        raise ValueError(f"area must be 1..{AREA_COUNT}, got {area}")


def find_table_dir(start: str | Path | None = None) -> Path:
    """Find the copied client's JanQ table directory from a workspace path."""

    current = Path(start or Path.cwd()).resolve()
    candidates: list[Path] = []
    for base in (current, *current.parents):
        candidates.append(
            base
            / "sega_net_MJ"
            / "MJ"
            / "MJ_Data"
            / "StreamingAssets"
            / "Janq"
            / "table"
        )

    for candidate in candidates:
        if all((candidate / filename).is_file() for filename in NYUKYU_FILENAMES):
            return candidate

    checked = "\n".join(str(path) for path in candidates)
    raise FileNotFoundError(f"could not find JanQ table dir; checked:\n{checked}")


def load_tables(table_dir: str | Path | None = None) -> dict[str, NyukyuTable]:
    base_dir = Path(table_dir) if table_dir is not None else find_table_dir()
    return {
        filename: NyukyuTable.from_path(base_dir / filename)
        for filename in NYUKYU_FILENAMES
    }


def assert_same_hash(tables: Iterable[NyukyuTable]) -> str:
    hashes = {table.sha256 for table in tables}
    if len(hashes) != 1:
        raise NyukyuTableError(f"nyukyu table hashes differ: {sorted(hashes)}")
    return next(iter(hashes))


def main() -> None:
    tables = load_tables()
    shared_hash = assert_same_hash(tables.values())
    print(f"loaded {len(tables)} nyukyu tables; shared sha256={shared_hash}")
    base = tables["nyukyu_base_table.bytes"]
    for area in range(1, AREA_COUNT + 1):
        print(f"area {area}: {base.describe_area(area)}")


if __name__ == "__main__":
    main()

