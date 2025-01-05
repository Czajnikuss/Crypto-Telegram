from binance.client import Client



from binance.client import Client
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from dotenv import load_dotenv
import os

# Wczytaj zmienne środowiskowe z pliku .env
load_dotenv()

# Pobierz dane z .env
testmode = os.getenv('TESTMODE', 'false').lower() == 'true'  # Domyślnie false
api_key = os.getenv('BINANCE_API_KEY')
api_secret = os.getenv('BINANCE_API_SECRET')

# Inicjalizacja klienta Binance
if testmode:
    print("Używanie środowiska testowego Binance (Testnet).")
    client = Client(api_key, api_secret, testnet=True)
else:
    print("Używanie środowiska produkcyjnego Binance.")
    client = Client(api_key, api_secret)

def get_all_balances():
    """
    Pobiera salda wszystkich dostępnych walut na koncie.
    """
    try:
        account_info = client.get_account()
        balances = account_info['balances']
        return {balance['asset']: float(balance['free']) for balance in balances if float(balance['free']) > 0}
    except Exception as e:
        print(f"Błąd podczas pobierania sald: {e}")
        return {}
    
    
def place_order(symbol, side, quantity):
    side = SIDE_SELL if side == "SELL" else SIDE_BUY
    
    take_profit_order = client.create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )
    print(take_profit_order['orderId'])
    print(take_profit_order['status'])
    return take_profit_order

def reset_account(target_usdc=10000):
    """
    Resetuje konto, sprzedając wszystkie dostępne waluty i kupując USDC, aby osiągnąć docelowe saldo.
    """
    # Pobierz wszystkie salda
    balances = get_all_balances()
    if not balances:
        print("Brak dostępnych środków na koncie.")
        return

    # Sprawdź, czy już mamy wystarczająco USDC
    current_usdc = balances.get('USDC', 0)
    if current_usdc >= target_usdc:
        print(f"Saldo USDC jest już wystarczające: {current_usdc} USDC.")
        return

    # Sprzedaj wszystkie waluty (oprócz USDC) i kup USDC
    for asset, balance in balances.items():
        if asset == 'USDC':
            continue  # Pomijamy USDC

        # Sprawdź, czy para handlowa istnieje (np. BTCUSDC, ETHUSDC)
        symbol = f"{asset}USDC"
        try:
            symbol_info = client.get_symbol_info(symbol)
            if not symbol_info:
                print(f"Para handlowa {symbol} nie istnieje. Pomijam {asset}.")
                continue

            # Pobierz aktualną cenę
            ticker = client.get_symbol_ticker(symbol=symbol)
            current_price = float(ticker['price'])

            # Oblicz ilość do sprzedania
            quantity = balance

            # Sprawdź wymagania LOT_SIZE
            lot_size_filter = next(filter(lambda f: f['filterType'] == 'LOT_SIZE', symbol_info['filters']))
            min_qty = float(lot_size_filter['minQty'])
            step_size = float(lot_size_filter['stepSize'])

            # Dostosuj ilość do wymagań LOT_SIZE
            quantity = max(min_qty, quantity)
            quantity = round(quantity // step_size * step_size, 8)

            if quantity <= 0:
                print(f"Nie można sprzedać {asset} (ilość zbyt mała).")
                continue

            # Wykonaj zlecenie marketowe (sprzedaż)
            take_profit_order = client.create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            print(f"Sprzedano {quantity} {asset} za USDC: {take_profit_order}")

        except Exception as e:
            print(f"Błąd podczas sprzedaży {asset}: {e}")

    # Sprawdź końcowe saldo USDC
    final_balances = get_all_balances()
    final_usdc = final_balances.get('USDC', 0)
    print(f"Końcowe saldo USDC: {final_usdc} USDC.")
    
    
def get_algo_orders_count(symbol):
    """
    Pobiera liczbę aktywnych zleceń algorytmicznych dla danej pary handlowej.
    """
    try:
        open_orders = client.get_open_orders(symbol=symbol)
        algo_orders = [take_profit_order for take_profit_order in open_orders if take_profit_order['type'] in ['STOP_LOSS', 'TAKE_PROFIT']]
        return len(algo_orders)
    except Exception as e:
        print(f"Błąd podczas pobierania aktywnych zleceń: {e}")
        return 0
    
def set_stop_loss_order(symbol, side, quantity, stopPrice=0):
    avg_price = float(client.get_avg_price(symbol=symbol)['price'])
    take_profit_order = client.create_order(
                symbol=symbol,
                side=SIDE_BUY if side == SIDE_SELL else SIDE_SELL,
                type="STOP_LOSS",  # Używamy STOP_LOSS
                quantity=quantity,
                stopPrice=stopPrice if stopPrice!= 0 else adjust_price_precision(symbol, avg_price * 1.05) if side == SIDE_SELL else adjust_price_precision(symbol, avg_price * 0.95)
            )
    print(take_profit_order)
    return take_profit_order

def set_take_profit_order(symbol, side, quantity, stopPrice=0):
    avg_price = float(client.get_avg_price(symbol=symbol)['price'])
    take_profit_order = client.create_order(
                symbol=symbol,
                side=SIDE_BUY if side == SIDE_SELL else SIDE_SELL,
                type="TAKE_PROFIT",  # Używamy STOP_LOSS
                quantity=quantity,
                stopPrice=stopPrice if stopPrice!= 0 else adjust_price_precision(symbol, avg_price * 0.95) if side == SIDE_SELL else adjust_price_precision(symbol, avg_price * 1.05)
            )
    print(take_profit_order)
    return take_profit_order

def adjust_price_precision(symbol, price):
    """
    Dostosowuje precyzję ceny do wymagań symbolu.
    """
    symbol_info = client.get_symbol_info(symbol)
    price_filter = next(filter(lambda f: f['filterType'] == 'PRICE_FILTER', symbol_info['filters']))
    tick_size = float(price_filter['tickSize'])
    
    # Oblicz liczbę miejsc dziesiętnych na podstawie tickSize
    precision = len(str(tick_size).rstrip('0').split('.')[1]) if '.' in str(tick_size) else 0
    return float('{:.{}f}'.format(price, precision))
    
def get_order_all(symbol, orderId):
    params = {
            'symbol': symbol,
            'orderId': orderId
        }
    order = client.get_order(**params)
    return order
        
# Uruchom funkcję reset_account
#reset_account()
#print(get_all_balances())
#print(client.get_open_orders())
#take_profit_order = place_order("OMNIUSDT", "SELL", 1)
#orderId= take_profit_order['orderId']


#set_stop_loss_order("OMNIUSDT", SIDE_SELL, 1)

#print(get_order_all("OMNIUSDT", 1394960))
print (set_take_profit_order("OMNIUSDT", SIDE_SELL, 1))

