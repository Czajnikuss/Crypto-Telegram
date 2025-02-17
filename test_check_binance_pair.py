import unittest
from common import check_binance_pair_and_price, currency_aliases


class DummyClient:
    def __init__(self, ticker_mapping, exchange_symbols):
        self.ticker_mapping = ticker_mapping
        self.exchange_symbols = exchange_symbols

    def get_exchange_info(self):
        return {"symbols": [{"symbol": s} for s in self.exchange_symbols]}

    def get_ticker(self, symbol):
        # Return lastPrice as a string
        price = self.ticker_mapping.get(symbol, "100")
        return {"lastPrice": str(price)}


class TestCheckBinancePairAndPrice(unittest.TestCase):
    def test_success(self):
        # For BTC/USDT, primary pair BTCUSDT exists and price equals entry level
        client = DummyClient(ticker_mapping={"BTCUSDT": 100}, exchange_symbols=["BTCUSDT"])
        result = check_binance_pair_and_price(client, "BTC/USDT", 100)
        self.assertTrue(result.get("success"))
        self.assertEqual(result["symbol"], "BTCUSDT")
        self.assertEqual(result["price"], 100)

    def test_deviation_error(self):
        # Price deviates more than 15%
        client = DummyClient(ticker_mapping={"BTCUSDT": 120}, exchange_symbols=["BTCUSDT"])
        result = check_binance_pair_and_price(client, "BTC/USDT", 100)
        self.assertIn("error", result)
        self.assertAlmostEqual(result["price"], 120)

    def test_pair_not_found(self):
        # No valid pair available
        client = DummyClient(ticker_mapping={}, exchange_symbols=[])
        result = check_binance_pair_and_price(client, "NON/USDT", 100)
        self.assertIn("error", result)
        self.assertIsNone(result["price"])

    def test_alias_substitution(self):
        # For BTC, if BTCUSDT is not available but alias XBTUSDT is available
        client = DummyClient(ticker_mapping={"XBTUSDT": 100}, exchange_symbols=["XBTUSDT"])
        result = check_binance_pair_and_price(client, "BTC/USDT", 100)
        self.assertTrue(result.get("success"))
        self.assertEqual(result["symbol"], "XBTUSDT")

    def test_special_case(self):
        # For special case: input "1000SHIB/USDT" should try alternative alias
        # base_currency becomes "1000SHIB", then base_without_prefix becomes "SHIB".
        # According to currency_aliases, "SHIB": ["SHIBAINU"] so candidate becomes "1000SHIBAINUUSDT".
        client = DummyClient(ticker_mapping={"1000SHIBAINUUSDT": 100}, exchange_symbols=["1000SHIBAINUUSDT"])
        result = check_binance_pair_and_price(client, "1000SHIB/USDT", 100)
        self.assertTrue(result.get("success"))
        self.assertEqual(result["symbol"], "1000SHIBAINUUSDT")


if __name__ == '__main__':
    unittest.main()
