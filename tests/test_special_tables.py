import unittest

from janq_lab.assets.nyukyu import find_table_dir
from janq_lab.assets.special import load_special_tables


class SpecialTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = load_special_tables(find_table_dir())

    def test_select_tables(self):
        self.assertEqual(tuple((number, 1) for number in range(2, 13)), self.tables.paren_select)
        self.assertEqual(((99, 1),), self.tables.yakuman_select)

    def test_paren_tables_have_five_records_with_dora(self):
        self.assertEqual(set(range(2, 13)), set(self.tables.paren_tables))
        table = self.tables.paren_tables[2]
        self.assertEqual(5, len(table.records))
        first = table.records[0]
        self.assertTrue(first.enabled)
        self.assertEqual(13, len(first.tiles))
        self.assertEqual(33, first.dora_id)
        self.assertEqual(0, first.ura_dora_id)

    def test_yakuman_tables_have_expected_record_shapes(self):
        self.assertEqual(5, len(self.tables.yakuman_records))
        self.assertEqual(13, len(self.tables.yakuman_records[0].tiles))
        self.assertEqual((0, 8, 9, 17, 26), self.tables.yakuman_records[0].tiles[:5])
        self.assertEqual(5, len(self.tables.yakuman_tenho_records))
        self.assertEqual(14, len(self.tables.yakuman_tenho_records[0].tiles))

    def test_doukei_tables_preserved_raw(self):
        self.assertEqual((33, 34, 33), self.tables.doukei_select)
        self.assertEqual(14, len(self.tables.doukei_table))


if __name__ == "__main__":
    unittest.main()

