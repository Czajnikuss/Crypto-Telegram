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
            order = client.create_order(
                symbol=symbol,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
            print(f"Sprzedano {quantity} {asset} za USDC: {order}")

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
        algo_orders = [order for order in open_orders if order['type'] in ['STOP_LOSS', 'TAKE_PROFIT']]
        return len(algo_orders)
    except Exception as e:
        print(f"Błąd podczas pobierania aktywnych zleceń: {e}")
        return 0

# Uruchom funkcję reset_account
#reset_account()
print(get_all_balances())
#print(client.get_open_orders())