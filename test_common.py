import unittest
from common import adjust_quantity, adjust_price


class TestCommonFunctions(unittest.TestCase):
    def test_adjust_quantity_below_min(self):
        # For symbol "BTCUSDT", minQty = 0.001, stepSize = 0.001 from our dummy client implementation
        result = adjust_quantity("BTCUSDT", 0.0005)
        self.assertEqual(result, 0.001)

    def test_adjust_quantity_rounding(self):
        # Using quantity 1.234567 should round to 1.235
        result = adjust_quantity("BTCUSDT", 1.234567)
        self.assertEqual(result, 1.235)

    def test_adjust_price_rounding_down(self):
        # With tickSize = 0.01, 1.234 should round down to 1.23
        result = adjust_price("BTCUSDT", 1.234)
        self.assertEqual(result, 1.23)

    def test_adjust_price_rounding_up(self):
        # With tickSize = 0.01, 1.237 should round up to 1.24
        result = adjust_price("BTCUSDT", 1.237)
        self.assertEqual(result, 1.24)


if __name__ == '__main__':
    unittest.main()
