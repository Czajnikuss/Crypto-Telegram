from binance.client import Client
from dotenv import load_dotenv
import os, math, time, json
from datetime import datetime
from telethon import TelegramClient


SIGNAL_HISTORY_FILE = 'signal_history.json'
MAX_HISTORY_SIZE = 50  # Maksymalna liczba sygnałów w historii

last_message_ids = set()

def load_env_variables():
    load_dotenv()
    api_id = os.getenv('API_ID')
    api_hash = os.getenv('API_HASH')
    return api_id, api_hash

def create_telegram_client(session_name):
    api_id, api_hash = load_env_variables()
    return TelegramClient(session_name, api_id, api_hash)


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

def check_binance_pair_and_price(client, pair, entry_level):
    """
    Sprawdza dostępność pary na Binance i porównuje cenę z poziomem wejścia.
    
    Args:
        client: Skonfigurowany klient Binance API
        pair: Para walutowa z sygnału (np. "BSV/USDT")
        entry_level: Cena wejścia z sygnału
        
    Returns:
        dict: Wynik sprawdzenia zawierający status i ewentualnie cenę lub błąd
    """
    try:
        # Pobierz wszystkie dostępne pary handlowe z Binance
        exchange_info = client.get_exchange_info()
        symbols = [symbol['symbol'] for symbol in exchange_info['symbols']]

        # Usuń znak '/' z pary, aby dopasować do formatu Binance
        formatted_pair = pair.replace('/', '')
        
        # Sprawdź alternatywne oznaczenia dla BSV
        possible_pairs = [formatted_pair]
        if 'BSV' in formatted_pair:
            # BSV to Bitcoin SV, który może być oznaczony jako BCHSV na niektórych giełdach
            possible_pairs.append(formatted_pair.replace('BSV', 'BCHSV'))

        found_pair = None
        for test_pair in possible_pairs:
            if test_pair in symbols:
                found_pair = test_pair
                break

        if not found_pair:
            return {
                "error": f"Para {pair} nie jest dostępna na Binance.",
                "price": None
            }

        # Pobierz aktualną cenę dla pary
        ticker = client.get_ticker(symbol=found_pair)
        current_price = float(ticker['lastPrice'])

        # Sprawdź, czy cena odbiega od poziomu wejścia o więcej niż 10-15%
        deviation = abs((current_price - entry_level) / entry_level) * 100
        if deviation > 15:
            return {
                "error": f"Cena {found_pair} odbiega od poziomu wejścia o {deviation:.2f}%.",
                "price": current_price
            }

        return {
            "success": True,
            "symbol": found_pair,
            "price": current_price
        }

    except Exception as e:
        return {
            "error": str(e),
            "price": None
        }


        
def load_signal_history():
    """
    Ładuje historię sygnałów z pliku JSON.
    """
    if os.path.exists(SIGNAL_HISTORY_FILE):
        with open(SIGNAL_HISTORY_FILE, 'r') as file:
            return json.load(file)
    return []

def save_signal_history(history):
    """
    Zapisuje historię sygnałów do pliku JSON.
    """
    with open(SIGNAL_HISTORY_FILE, 'w') as file:
        json.dump(history, file, indent=4)

def is_signal_new(signal, history):
    """
    Sprawdza, czy sygnał jest nowy (nie istnieje w historii).
    """
    for existing_signal in history:
        if (existing_signal["currency"] == signal["currency"] and
            existing_signal["signal_type"] == signal["signal_type"] and
            existing_signal["entry"] == signal["entry"] and
            existing_signal["stop_loss"] == signal["stop_loss"] and
            existing_signal["targets"] == signal["targets"]):
            return False
    return True
