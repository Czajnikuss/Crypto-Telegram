from binance.client import Client
from dotenv import load_dotenv
import os, math, time
from datetime import datetime

def log_to_file(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("logfile.txt", "a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")

load_dotenv()
testmode = os.getenv('TESTMODE', 'false').lower() == 'true'
api_key = os.getenv('BINANCE_API_KEY')
api_secret = os.getenv('BINANCE_API_SECRET')

if testmode:
    client = Client(api_key, api_secret, testnet=True)
else:
    client = Client(api_key, api_secret)

def adjust_quantity(symbol: str, quantity: float) -> float:
    """Dostosowuje ilość do wymogów LOT_SIZE"""
    symbol_info = client.get_symbol_info(symbol)
    lot_filter = next(filter(lambda x: x['filterType'] == 'LOT_SIZE', symbol_info['filters']))
    
    step_size = float(lot_filter['stepSize'])
    min_qty = float(lot_filter['minQty'])
    
    precision = int(round(-math.log(step_size, 10), 0))
    quantity = max(min_qty, quantity)
    quantity = round(quantity / step_size) * step_size
    
    return float('{:.{}f}'.format(quantity, precision))

def adjust_price(symbol: str, price: float) -> float:
    """Dostosowuje cenę do wymogów PRICE_FILTER"""
    symbol_info = client.get_symbol_info(symbol)
    price_filter = next(filter(lambda x: x['filterType'] == 'PRICE_FILTER', symbol_info['filters']))
    
    tick_size = float(price_filter['tickSize'])
    precision = int(round(-math.log(tick_size, 10), 0))
    price = round(price / tick_size) * tick_size
    
    return float('{:.{}f}'.format(price, precision))

def get_order_details(symbol: str, order_id: int, max_retries: int = 3) -> dict:
    """Pobiera szczegóły zlecenia z obsługą ponownych prób"""
    for attempt in range(max_retries):
        try:
            time.sleep(2 ** attempt)  # Exponential backoff
            order = client.get_order(**{
                'symbol': symbol,
                'orderId': order_id
            })
            return order
        except Exception as e:
            if attempt == max_retries - 1:
                log_to_file(f"Błąd podczas pobierania szczegółów zlecenia {order_id} (próba {attempt+1}/{max_retries}): {str(e)}")
                return None
            continue
