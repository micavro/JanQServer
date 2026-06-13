import unittest

from janq_lab.probe.normalize import (
    api_tile_to_model,
    api_tiles_to_model,
    normalize_haipai_payload,
    normalize_tsumo_payload,
)


class ProbeNormalizeTests(unittest.TestCase):
    def test_api_tile_to_model(self):
        self.assertEqual(0, api_tile_to_model(1))
        self.assertEqual(33, api_tile_to_model(34))
        self.assertIsNone(api_tile_to_model(0))
        self.assertIsNone(api_tile_to_model(9999))
        self.assertIsNone(api_tile_to_model(True))
        self.assertIsNone(api_tile_to_model("1"))

    def test_api_tiles_to_model_filters_placeholders(self):
        self.assertEqual((0, 1, 33), api_tiles_to_model([1, 2, 0, 9999, 34]))

    def test_normalize_payloads(self):
        haipai = normalize_haipai_payload(
            {"haipai": [1, 2, 34], "omoDora": 5, "uraDora": 0, "tsumo": 33}
        )
        self.assertEqual((0, 1, 33), haipai["haipai"])
        self.assertEqual(4, haipai["dora"])
        self.assertIsNone(haipai["ura_dora"])
        self.assertEqual(32, haipai["tsumo"])

        tsumo = normalize_tsumo_payload(
            {"pai": 31, "tehai": [1, 31], "omo_dora": 9, "ura_dora": 10}
        )
        self.assertEqual(30, tsumo["pai"])
        self.assertEqual((0, 30), tsumo["tehai"])
        self.assertEqual(8, tsumo["dora"])
        self.assertEqual(9, tsumo["ura_dora"])


if __name__ == "__main__":
    unittest.main()

