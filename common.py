from binance.client import Client
from dotenv import load_dotenv
import os, math, time, json
from datetime import datetime
from telethon import TelegramClient
import requests


SIGNAL_HISTORY_FILE = 'signal_history.json'
MAX_HISTORY_SIZE = 50  # Maksymalna liczba sygnałów w historii

last_message_ids = set()

currency_aliases = {
    "BSV": ["BCHSV"],     # Bitcoin SV
    "BTC": ["XBT"],       # Bitcoin
    "ETH": ["ETH2"],      # Ethereum (z uwzględnieniem ETH 2.0)
    "DOGE": ["XDG"],      # Dogecoin
    "USDT": ["TETHER"],   # Tether
    "BCH": ["BCC"],       # Bitcoin Cash
    "XRP": ["RIPPLE"],    # Ripple
    "IOTA": ["MIOTA"],    # IOTA
    "LUNA": ["LUNC"],     # Luna Classic
    "UST": ["USTC"],      # TerraUSD Classic
    "SHIB": ["SHIBAINU"], # Shiba Inu
    "DOT": ["POLKADOT"],  # Polkadot
    "LINK": ["CHAINLINK"], # Chainlink
    "GRIFFAI": ["GRIFFAIN"], # Griffai/Griffain
    "GRIFFAIN": ["GRIFFAI"] # Dodajemy też w drugą stronę dla pewności
}




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

        # Usuń znak '/' z pary i rozdziel na base i quote
        formatted_pair = pair.replace('/', '')
        base_currency = formatted_pair.replace('USDT', '')
        
        # Lista możliwych oznaczeń pary
        possible_pairs = [formatted_pair]
        
        # Dodaj alternatywne oznaczenia z currency_aliases
        if base_currency in currency_aliases:
            for alias in currency_aliases[base_currency]:
                possible_pairs.append(f"{alias}USDT")
        
        # Sprawdź specjalne przypadki (np. 1000SHIB)
        if base_currency.startswith('1000'):
            base_without_prefix = base_currency[4:]
            if base_without_prefix in currency_aliases:
                for alias in currency_aliases[base_without_prefix]:
                    possible_pairs.append(f"1000{alias}USDT")

        # Znajdź pierwszą dostępną parę
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
            existing_signal["date"] == signal["date"] and
            existing_signal["targets"] == signal["targets"]):
            return False
    return True


def create_oco_order_direct(client, symbol, side, quantity, take_profit_price, stop_price, stop_limit_price):
    """
    Tworzy zlecenie OCO bezpośrednio przez API Binance.
    
    Args:
        client: Skonfigurowany klient Binance API
        symbol: Symbol pary tradingowej (np. 'BTCUSDT')
        side: Strona zlecenia ('BUY' lub 'SELL')
        quantity: Ilość do handlu
        take_profit_price: Cena take profit
        stop_price: Cena stop loss (trigger)
        stop_limit_price: Cena limit dla stop loss
    
    Returns:
        dict: Odpowiedź z API Binance
    """
    try:
        # Przygotuj parametry
        params = {
            'symbol': symbol,
            'side': side,
            'quantity': adjust_quantity(symbol, quantity),
            'aboveType': 'LIMIT_MAKER',
            'belowType': 'STOP_LOSS_LIMIT',
            'abovePrice': adjust_price(symbol, take_profit_price),
            'belowPrice': adjust_price(symbol, stop_limit_price),
            'belowStopPrice': adjust_price(symbol, stop_price),
            'belowTimeInForce': 'GTC',
            'timestamp': int(time.time() * 1000)
        }
        
        # Generuj podpis
        query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
        signature = client._generate_signature(query_string)
        params['signature'] = signature
        
        # Przygotuj nagłówki
        headers = {
            'X-MBX-APIKEY': client.API_KEY
        }
        
        # Wykonaj request
        response = requests.post(
            f'{client.API_URL}/api/v3/orderList/oco',
            params=params,
            headers=headers
        )
        
        if response.status_code != 200:
            log_to_file(f"Błąd podczas tworzenia zlecenia OCO: {response.text}")
            return None
            
        return response.json()
        
    except Exception as e:
        log_to_file(f"Wyjątek podczas tworzenia zlecenia OCO: {str(e)}")
        return None

def verify_oco_order(client, symbol, order_list_id):
    """
    Weryfikuje status zlecenia OCO.
    
    Args:
        client: Skonfigurowany klient Binance API
        symbol: Symbol pary tradingowej
        order_list_id: ID listy zleceń OCO
    
    Returns:
        dict: Status zlecenia OCO
    """
    try:
        params = {
            'orderListId': order_list_id,
            'timestamp': int(time.time() * 1000)
        }
        
        query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
        signature = client._generate_signature(query_string)
        params['signature'] = signature
        
        headers = {
            'X-MBX-APIKEY': client.API_KEY
        }
        
        response = requests.get(
            f'{client.API_URL}/api/v3/orderList',
            params=params,
            headers=headers
        )
        
        if response.status_code != 200:
            log_to_file(f"Błąd podczas weryfikacji zlecenia OCO: {response.text}")
            return None
            
        return response.json()
        
    except Exception as e:
        log_to_file(f"Wyjątek podczas weryfikacji zlecenia OCO: {str(e)}")
        return None

