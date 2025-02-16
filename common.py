from binance.client import Client
from dotenv import load_dotenv
import os, math, time, json
from datetime import datetime
from telethon import TelegramClient
import requests
from urllib.parse import urlencode, quote

from openai import OpenAI

import traceback
import hmac, hashlib

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
    
def test_api_keys():
    log_to_file("=== Testing API Keys ===")
    log_to_file(f"API Key: {api_key[:5]}...{api_key[-5:]}")  # Logujemy tylko fragmenty dla bezpieczeństwa
    log_to_file(f"API Secret: {api_secret[:5]}...{api_secret[-5:]}")
    
    # Test połączenia
    try:
        # Sprawdź uprawnienia konta
        account_info = client.get_account()
        log_to_file("Account permissions:")
        log_to_file(str(account_info.get('permissions', [])))
        
        # Sprawdź czas serwera
        server_time = client.get_server_time()
        log_to_file(f"Server time: {server_time}")
        
        return True
    except Exception as e:
        log_to_file(f"Error testing API keys: {str(e)}")
        return False



def get_all_oco_orders_for_symbol(client, symbol, only_active=False):
    """
    Pobiera i filtruje zlecenia OCO dla danego symbolu, z opcją filtrowania tylko aktywnych,
    oraz dodaje informacje o statusach zleceń.

    Args:
        client: Obiekt klienta Binance.
        symbol (str): Symbol pary handlowej (np. "BTCUSDT").
        only_active (bool): Jeśli True, zwraca tylko aktywne zlecenia.

    Returns:
        list: Lista słowników z informacjami o zleceniach OCO, lub None w przypadku błędu.
              Każdy słownik zawiera strukturę podobną do odpowiedzi API przy tworzeniu zlecenia OCO.
    """
    all_oco_orders = get_all_oco_orders(client)  # Używamy wcześniej zdefiniowanej funkcji
    if not all_oco_orders:
        return None  # W przypadku błędu w get_all_oco_orders

    filtered_oco_orders = [
        order for order in all_oco_orders if order['symbol'] == symbol
    ]

    if only_active:
        # Filtracja aktywnych zleceń. Status 'NEW', 'PARTIALLY_FILLED', 'PENDING_CANCEL' są aktywne
        active_statuses = ['NEW', 'PARTIALLY_FILLED', 'PENDING_CANCEL']
        filtered_oco_orders = [
            order for order in filtered_oco_orders
            if order['listStatusType'] in ['EXEC_STARTED', 'ALL_DONE'] and # Sprawdzamy status całej listy
            any(report['status'] in active_statuses for report in get_order_reports(client, order['orderListId'],symbol)) # Sprawdzamy status każdego zlecenia
        ]

    # Dodawanie statusów zleceń (orderReports)
    for oco_order in filtered_oco_orders:
      oco_order['orderReports'] = get_order_reports(client, oco_order['orderListId'],symbol)

    return filtered_oco_orders

def get_all_oco_orders(client):
    """
    Pobiera wszystkie zlecenia OCO dla konta, używając bezpośrednich zapytań HTTP do API Binance.
    """
    try:
        # Create base parameters (bez symbolu)
        params = {
            'timestamp': int(time.time() * 1000)
        }

        # Create query string with proper encoding
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])

        # Generate signature
        signature = hmac.new(
            bytes(client.API_SECRET, 'utf-8'),
            bytes(query_string, 'utf-8'),
            hashlib.sha256
        ).hexdigest()

        # Add signature to params after generating it
        params['signature'] = signature

        headers = {
            'X-MBX-APIKEY': client.API_KEY
        }

        base_url = 'https://testnet.binance.vision' if testmode else 'https://api.binance.com' # Używaj globalnej zmiennej testmode

        # Log full request details
        #log_to_file("=== Request Details - Get All OCOs ===")
        #log_to_file(f"Query string for signature: {query_string}")
        #log_to_file(f"Generated signature: {signature}")

        response = requests.get(
            f'{base_url}/api/v3/allOrderList',  # Endpoint do pobierania wszystkich OCO
            params=params,
            headers=headers
        )

        #log_to_file("=== Response Details - Get All OCOs ===")
        #log_to_file(f"Status Code: {response.status_code}")
        #log_to_file(f"Response Text: {response.text}")
        #log_to_file(f"Request URL: {response.url}")

        if response.status_code != 200:
            log_to_file(f"Błąd podczas pobierania OCO zleceń: {response.text}")
            return None

        json_response = response.json()
        #log_to_file(f"Odpowiedź z serwera Binance: {json.dumps(json_response, indent=2)}")

        return json_response  # Zwracamy całą odpowiedź JSON

    except Exception as e:
        error_trace = traceback.format_exc()
        log_to_file(f"Exception: {str(e)}")
        log_to_file(f"Stack trace:\n{error_trace}")
        return None

def get_order_reports(client, orderListId, symbol):
    """
    Pobiera statusy zleceń dla danego orderListId, używając GET /api/v3/allOrders.
    """
    try:
        # Create base parameters
        params = {
            'symbol': symbol,
            'timestamp': int(time.time() * 1000)
        }

        # Create query string with proper encoding
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])

        # Generate signature
        signature = hmac.new(
            bytes(client.API_SECRET, 'utf-8'),
            bytes(query_string, 'utf-8'),
            hashlib.sha256
        ).hexdigest()

        # Add signature to params after generating it
        params['signature'] = signature

        headers = {
            'X-MBX-APIKEY': client.API_KEY
        }

        base_url = 'https://testnet.binance.vision' if testmode else 'https://api.binance.com'

        # Log full request details
        #log_to_file("=== Request Details - Get All Orders ===")
        #log_to_file(f"Query string for signature: {query_string}")
        #log_to_file(f"Generated signature: {signature}")

        response = requests.get(
            f'{base_url}/api/v3/allOrders',  # Endpoint do pobierania wszystkich zleceń
            params=params,
            headers=headers
        )

       # log_to_file("=== Response Details - Get All Orders ===")
        #log_to_file(f"Status Code: {response.status_code}")
        #log_to_file(f"Response Text: {response.text}")
        #log_to_file(f"Request URL: {response.url}")

        if response.status_code != 200:
            log_to_file(f"Błąd podczas pobierania zleceń: {response.text}")
            return []  # Zwracamy pustą listę w przypadku błędu

        all_orders = response.json()

        # Filtrujemy zlecenia, aby znaleźć tylko te z danego orderListId
        order_reports = [order for order in all_orders if order.get('orderListId') == orderListId]

        #log_to_file(f"Odpowiedź z serwera Binance: {json.dumps(order_reports, indent=2)}")

        return order_reports  # Zwracamy listę statusów zleceń

    except Exception as e:
        error_trace = traceback.format_exc()
        log_to_file(f"Exception: {str(e)}")
        log_to_file(f"Stack trace:\n{error_trace}")
        return []  # Zwracamy pustą listę w przypadku błędu



def get_oco_order_by_orderListId(client, orderListId):
    """
    Pobiera informacje o zleceniu OCO na podstawie orderListId, używając bezpośrednich zapytań HTTP do API Binance.
    """
    try:
        # Create base parameters
        params = {
            'orderListId': orderListId,
            'timestamp': int(time.time() * 1000)
        }

        # Create query string with proper encoding
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])

        # Generate signature
        signature = hmac.new(
            bytes(client.API_SECRET, 'utf-8'),
            bytes(query_string, 'utf-8'),
            hashlib.sha256
        ).hexdigest()

        # Add signature to params after generating it
        params['signature'] = signature

        headers = {
            'X-MBX-APIKEY': client.API_KEY
        }

        base_url = 'https://testnet.binance.vision' if testmode else 'https://api.binance.com'  # Używaj globalnej zmiennej testmode

        # Log full request details
        #log_to_file("=== Request Details - Get OCO by ID ===")
        #log_to_file(f"Query string for signature: {query_string}")
        #log_to_file(f"Generated signature: {signature}")

        response = requests.get(
            f'{base_url}/api/v3/orderList',  # Endpoint do pobierania OCO po ID
            params=params,
            headers=headers
        )

        #log_to_file("=== Response Details - Get OCO by ID ===")
        #log_to_file(f"Status Code: {response.status_code}")
        #log_to_file(f"Response Text: {response.text}")
        #log_to_file(f"Request URL: {response.url}")

        if response.status_code != 200:
            log_to_file(f"Błąd podczas pobierania OCO zlecenia: {response.text}")
            return None

        json_response = response.json()
        #log_to_file(f"Odpowiedź z serwera Binance: {json.dumps(json_response, indent=2)}")

        return json_response # Zwracamy całą odpowiedź JSON

    except Exception as e:
        error_trace = traceback.format_exc()
        log_to_file(f"Exception: {str(e)}")
        log_to_file(f"Stack trace:\n{error_trace}")
        return None

    
def create_oco_order_direct(client, symbol, side, quantity, take_profit_price, stop_price, stop_limit_price):
    try:
        # Create base parameters in alphabetical order
        params = {
            'abovePrice': format(float(take_profit_price), 'f'),
            'aboveType': 'LIMIT_MAKER',
            'belowPrice': format(float(stop_limit_price), 'f'),
            'belowStopPrice': format(float(stop_price), 'f'),
            'belowTimeInForce': 'GTC',
            'belowType': 'STOP_LOSS_LIMIT',
            'quantity': format(float(quantity), 'f'),
            'side': side,
            'symbol': symbol,
            'timestamp': int(time.time() * 1000)
        }
        
        # Create query string with proper encoding
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
        
        # Generate signature
        signature = hmac.new(
            bytes(client.API_SECRET, 'utf-8'),
            bytes(query_string, 'utf-8'),
            hashlib.sha256
        ).hexdigest()

        # Add signature to params after generating it
        params['signature'] = signature
        
        headers = {
            'X-MBX-APIKEY': client.API_KEY,
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        base_url = 'https://testnet.binance.vision' if testmode else 'https://api.binance.com'
        
        # Log full request details
        log_to_file("=== Request Details ===")
        log_to_file(f"Query string for signature: {query_string}")
        log_to_file(f"Generated signature: {signature}")
        
        response = requests.post(
            f'{base_url}/api/v3/orderList/oco',
            params=params,
            headers=headers
        )
        
        log_to_file("=== Response Details ===")
        log_to_file(f"Status Code: {response.status_code}")
        log_to_file(f"Response Text: {response.text}")
        log_to_file(f"Request URL: {response.url}")
        
        if response.status_code != 200:
            log_to_file(f"Błąd podczas tworzenia OCO zlecenia: {response.text}")
            return None

        json_response = response.json()
        log_to_file(f"Odpowiedź z serwera Binance: {json.dumps(json_response, indent=2)}")

        # Dodaj pełną odpowiedź z Binance do zwracanego obiektu
        oco_order = {
            'orderListId': json_response.get('orderListId'),
            'contingencyType': json_response.get('contingencyType'),
            'listStatusType': json_response.get('listStatusType'),
            'listOrderStatus': json_response.get('listOrderStatus'),
            'listClientOrderId': json_response.get('listClientOrderId'),
            'transactionTime': json_response.get('transactionTime'),
            'symbol': json_response.get('symbol'),
            'orders': json_response.get('orders'),
            'orderReports': json_response.get('orderReports')
        }

        return oco_order

    except Exception as e:
        error_trace = traceback.format_exc()
        log_to_file(f"Exception: {str(e)}")
        log_to_file(f"Stack trace:\n{error_trace}")
        return None



def test_oco_order():
    # Using values from the log
    test_params = {
        'symbol': 'IOTAUSDT',
        'quantity': 35033.0,
        'take_profit_price': 0.2972,
        'stop_price': 0.2302,
        'stop_limit_price': 0.229
    }
    
    result = create_oco_order_direct(
        client=client,
        symbol=test_params['symbol'],
        side='SELL',
        quantity=test_params['quantity'],
        take_profit_price=test_params['take_profit_price'],
        stop_price=test_params['stop_price'],
        stop_limit_price=test_params['stop_limit_price']
    )
    
    return result


def ask_AI_to_fill_the_signal_fields(message_text, partial_signal_data):
    """
    Używa OpenAI API do uzupełnienia brakujących pól w sygnale tradingowym.
    """
    client = OpenAI()  # Automatycznie użyje OPENAI_API_KEY ze środowiska
    
    # Przygotowanie promptu
    system_prompt = """
    Jesteś ekspertem w analizie sygnałów tradingowych crypto. Twoim zadaniem jest przeanalizowanie wiadomości 
    i uzupełnienie brakujących pól w strukturze sygnału tradingowego dla Binance.
    Wymagane pola to:
    - currency (format: XXXUSDT)
    - signal_type (LONG lub SHORT)
    - entry (liczba zmiennoprzecinkowa)
    - targets (lista liczb zmiennoprzecinkowych)
    - stop_loss (liczba zmiennoprzecinkowa)
    
    Zasady:
    1. Dla LONG: stop_loss < entry < targets (rosnąco)
    2. Dla SHORT: targets (malejąco) < entry < stop_loss
    3. Jeśli nie możesz znaleźć wartości, użyj logiki:
       - Dla LONG: stop_loss = entry * 0.90
       - Dla SHORT: stop_loss = entry * 1.1
    """
    
    user_prompt = f"""
    Oto wiadomość z sygnałem:
    {message_text}
    
    Obecne dane sygnału (niektóre mogą być None):
    {json.dumps(partial_signal_data, indent=2)}
    
    Zwróć tylko brakujące pola w formacie JSON. Nie zmieniaj istniejących wartości. Nie zwracaj nic poza JSON.
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,  # Niższa temperatura dla bardziej deterministycznych odpowiedzi
            response_format={"type": "json_object"}
        )
        
        # Parsowanie odpowiedzi JSON
        new_fields = json.loads(response.choices[0].message.content)
        
        # Aktualizacja partial_signal_data tylko dla brakujących pól
        updated_signal = partial_signal_data.copy()
        for key, value in new_fields.items():
            if updated_signal.get(key) is None:
                updated_signal[key] = value
        
        # Walidacja uzupełnionych danych
        if updated_signal["signal_type"] == "LONG":
            if updated_signal["targets"]:
                updated_signal["targets"] = sorted(updated_signal["targets"])
        elif updated_signal["signal_type"] == "SHORT":
            if updated_signal["targets"]:
                updated_signal["targets"] = sorted(updated_signal["targets"], reverse=True)
        
        return updated_signal
        
    except Exception as e:
        print(f"Błąd podczas używania OpenAI API: {str(e)}")
        return partial_signal_data

