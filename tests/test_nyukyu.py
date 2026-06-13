from pathlib import Path
import unittest

from janq_lab.assets.nyukyu import (
    AREA_COUNT,
    EXPECTED_WEIGHT_SUM,
    NYUKYU_FILENAMES,
    NyukyuTable,
    assert_same_hash,
    find_table_dir,
    load_tables,
)


class NyukyuTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table_dir = find_table_dir(Path(__file__).resolve())
        cls.tables = load_tables(cls.table_dir)
        cls.base = cls.tables["nyukyu_base_table.bytes"]

    def test_all_expected_tables_load(self):
        self.assertEqual(set(NYUKYU_FILENAMES), set(self.tables))

    def test_tables_are_currently_identical(self):
        shared_hash = assert_same_hash(self.tables.values())
        self.assertEqual(
            "c129996fc9cc1008b7f5d8be2888c26dfd1a52d63823ef73d3218fb4aa880448",
            shared_hash,
        )

    def test_each_area_sums_to_10000(self):
        for table in self.tables.values():
            self.assertEqual(AREA_COUNT, len(table.areas))
            for area in range(1, AREA_COUNT + 1):
                self.assertEqual(EXPECTED_WEIGHT_SUM, sum(table.weights_for_area(area)))

    def test_known_area_weights(self):
        self.assertEqual(1200, self.base.tile_weight(1, 0))
        self.assertEqual(2000, self.base.tile_weight(1, 27))
        self.assertEqual(2000, self.base.tile_weight(4, 31))
        self.assertEqual(2000, self.base.tile_weight(4, 32))
        self.assertEqual(2000, self.base.tile_weight(4, 33))
        self.assertEqual(1200, self.base.tile_weight(7, 22))
        self.assertEqual(2000, self.base.tile_weight(7, 30))

    def test_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            NyukyuTable.from_bytes(b"\x00" * 8, name="bad")


if __name__ == "__main__":
    unittest.main()

