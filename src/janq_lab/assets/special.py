"""Parenchallenge/yakuman setup table parsers.

These small JanQ resource tables appear to describe special-mode starting
hands. The record shapes are inferred from the copied client assets and the
`Api.Container.HaipaiData` structure:

* `paren_N_table.bytes`: 5 records x 16 bytes
  * enabled flag
  * 13 tile ids
  * dora id
  * ura-dora id
* `yakuman_table.bytes`: 5 records x 14 bytes
  * enabled flag
  * 13 tile ids
* `yakuman_tenho_table.bytes`: 5 records x 15 bytes
  * enabled flag
  * 14 tile ids

All tile ids in these tables are 0-based JanQ ids.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import argparse
import json
import re
from typing import Any

from janq_lab.assets.nyukyu import find_table_dir
from janq_lab.tiles import TILE_COUNT, tile_name


PAREN_TABLE_RE = re.compile(r"^paren_(\d+)_table\.bytes$")
PAREN_RECORDS_PER_TABLE = 5
PAREN_RECORD_SIZE = 16
YAKUMAN_RECORDS = 5
YAKUMAN_RECORD_SIZE = 14
YAKUMAN_TENHO_RECORD_SIZE = 15


class SpecialTableError(ValueError):
    """Raised when a special-mode table does not match the expected shape."""


@dataclass(frozen=True)
class SpecialHandRecord:
    enabled: bool
    tiles: tuple[int, ...]
    dora_id: int | None = None
    ura_dora_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "tiles": self.tiles,
            "tile_names": tuple(tile_name(tile_id) for tile_id in self.tiles),
            "dora_id": self.dora_id,
            "ura_dora_id": self.ura_dora_id,
        }


@dataclass(frozen=True)
class ParenTable:
    number: int
    source: Path
    sha256: str
    records: tuple[SpecialHandRecord, ...]


@dataclass(frozen=True)
class SpecialTables:
    table_dir: Path
    paren_select: tuple[tuple[int, int], ...]
    paren_tables: dict[int, ParenTable]
    yakuman_select: tuple[tuple[int, int], ...]
    yakuman_records: tuple[SpecialHandRecord, ...]
    yakuman_tenho_records: tuple[SpecialHandRecord, ...]
    doukei_select: tuple[int, ...]
    doukei_table: tuple[int, ...]

    def to_summary(self) -> dict[str, Any]:
        return {
            "table_dir": str(self.table_dir),
            "paren_select": self.paren_select,
            "paren_tables": {
                number: len(table.records)
                for number, table in sorted(self.paren_tables.items())
            },
            "yakuman_select": self.yakuman_select,
            "yakuman_records": len(self.yakuman_records),
            "yakuman_tenho_records": len(self.yakuman_tenho_records),
            "doukei_select": self.doukei_select,
            "doukei_table": self.doukei_table,
        }


def load_special_tables(table_dir: str | Path | None = None) -> SpecialTables:
    base = Path(table_dir) if table_dir is not None else find_table_dir()
    paren_tables: dict[int, ParenTable] = {}
    for path in sorted(base.glob("paren_*_table.bytes")):
        match = PAREN_TABLE_RE.match(path.name)
        if not match:
            continue
        number = int(match.group(1))
        paren_tables[number] = parse_paren_table(path, number=number)

    return SpecialTables(
        table_dir=base,
        paren_select=parse_select_pairs((base / "paren_select_table.bytes").read_bytes()),
        paren_tables=paren_tables,
        yakuman_select=parse_select_pairs((base / "yakuman_select_table.bytes").read_bytes()),
        yakuman_records=parse_yakuman_table(base / "yakuman_table.bytes"),
        yakuman_tenho_records=parse_yakuman_tenho_table(base / "yakuman_tenho_table.bytes"),
        doukei_select=tuple((base / "doukei_select_table.bytes").read_bytes()),
        doukei_table=tuple((base / "doukei_table.bytes").read_bytes()),
    )


def parse_paren_table(path: str | Path, *, number: int | None = None) -> ParenTable:
    table_path = Path(path)
    data = table_path.read_bytes()
    expected = PAREN_RECORDS_PER_TABLE * PAREN_RECORD_SIZE
    if len(data) != expected:
        raise SpecialTableError(f"{table_path.name} must be {expected} bytes, got {len(data)}")

    records = []
    for offset in range(0, len(data), PAREN_RECORD_SIZE):
        chunk = data[offset : offset + PAREN_RECORD_SIZE]
        records.append(
            _record(
                enabled=chunk[0],
                tiles=chunk[1:14],
                dora_id=chunk[14],
                ura_dora_id=chunk[15],
                source=table_path.name,
            )
        )

    if number is None:
        match = PAREN_TABLE_RE.match(table_path.name)
        if match is None:
            raise SpecialTableError(f"cannot infer paren table number from {table_path.name}")
        number = int(match.group(1))

    return ParenTable(
        number=number,
        source=table_path,
        sha256=sha256(data).hexdigest(),
        records=tuple(records),
    )


def parse_yakuman_table(path: str | Path) -> tuple[SpecialHandRecord, ...]:
    return _parse_fixed_records(Path(path), record_size=YAKUMAN_RECORD_SIZE, tile_count=13)


def parse_yakuman_tenho_table(path: str | Path) -> tuple[SpecialHandRecord, ...]:
    return _parse_fixed_records(Path(path), record_size=YAKUMAN_TENHO_RECORD_SIZE, tile_count=14)


def parse_select_pairs(data: bytes) -> tuple[tuple[int, int], ...]:
    if len(data) % 2 != 0:
        raise SpecialTableError(f"select table length must be even, got {len(data)}")
    return tuple((data[i], data[i + 1]) for i in range(0, len(data), 2))


def _parse_fixed_records(path: Path, *, record_size: int, tile_count: int) -> tuple[SpecialHandRecord, ...]:
    data = path.read_bytes()
    expected = YAKUMAN_RECORDS * record_size
    if len(data) != expected:
        raise SpecialTableError(f"{path.name} must be {expected} bytes, got {len(data)}")

    records = []
    for offset in range(0, len(data), record_size):
        chunk = data[offset : offset + record_size]
        records.append(
            _record(
                enabled=chunk[0],
                tiles=chunk[1 : 1 + tile_count],
                source=path.name,
            )
        )
    return tuple(records)


def _record(
    *,
    enabled: int,
    tiles: bytes,
    source: str,
    dora_id: int | None = None,
    ura_dora_id: int | None = None,
) -> SpecialHandRecord:
    if enabled not in (0, 1):
        raise SpecialTableError(f"{source}: enabled flag must be 0 or 1, got {enabled}")
    tile_tuple = tuple(int(tile_id) for tile_id in tiles)
    for tile_id in tile_tuple:
        _validate_tile(tile_id, source)
    if dora_id is not None:
        _validate_tile(dora_id, source)
    if ura_dora_id is not None:
        _validate_tile(ura_dora_id, source)
    return SpecialHandRecord(
        enabled=bool(enabled),
        tiles=tile_tuple,
        dora_id=dora_id,
        ura_dora_id=ura_dora_id,
    )


def _validate_tile(tile_id: int, source: str) -> None:
    if not 0 <= tile_id < TILE_COUNT:
        raise SpecialTableError(f"{source}: tile id must be 0..33, got {tile_id}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Summarize JanQ special-mode tables.")
    parser.add_argument("--table-dir", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    tables = load_special_tables(args.table_dir)
    if args.json:
        print(json.dumps(tables.to_summary(), ensure_ascii=False, indent=2))
        return

    summary = tables.to_summary()
    print(f"table_dir: {summary['table_dir']}")
    print(f"paren_select: {summary['paren_select']}")
    print(f"paren_tables: {summary['paren_tables']}")
    print(f"yakuman_select: {summary['yakuman_select']}")
    print(f"yakuman_records: {summary['yakuman_records']}")
    print(f"yakuman_tenho_records: {summary['yakuman_tenho_records']}")
    print(f"doukei_select: {summary['doukei_select']}")
    print(f"doukei_table: {summary['doukei_table']}")


if __name__ == "__main__":
    main()

