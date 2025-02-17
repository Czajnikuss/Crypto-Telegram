class Client:
    def __init__(self, api_key, api_secret, testnet=False):
        self.API_KEY = api_key
        self.API_SECRET = api_secret
        self.testnet = testnet

    def get_symbol_info(self, symbol):
        return {
            "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"}
            ],
            "baseAsset": symbol[:-4] if symbol.endswith("USDT") else symbol,
            "quoteAsset": "USDT"
        }

    def get_account(self):
        return {"balances": []}
