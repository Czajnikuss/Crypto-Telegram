from binance.client import Client
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET, ORDER_TYPE_TAKE_PROFIT, ORDER_TYPE_STOP_LOSS
from dotenv import load_dotenv
import os, time
import logging
from datetime import datetime

def log_to_file(message):
    """
    Zapisuje wiadomość do pliku logfile.txt z timestampem.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("logfile.txt", "a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")

# Wczytaj zmienne środowiskowe z pliku .env
load_dotenv()
# Przechowuj otwarte pozycje
open_positions = {}

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

def get_available_balance(asset):
    """
    Pobiera dostępne saldo dla danej waluty.
    """
    try:
        balance = client.get_asset_balance(asset=asset)
        return float(balance['free'])  # Dostępne środki
    except Exception as e:
        print(f"Błąd podczas pobierania salda: {e}")
        return 0.0

def calculate_trade_amount(available_balance, percentage, symbol):
    """
    Oblicza kwotę transakcji jako procent dostępnych środków, uwzględniając wymagania LOT_SIZE.
    """
    try:
        symbol_info = client.get_symbol_info(symbol)
        if not symbol_info:
            log_to_file(f"Nie można pobrać informacji o symbolu {symbol}.")
            return 0.0

        # Pobierz wymagania LOT_SIZE
        lot_size_filter = next(filter(lambda f: f['filterType'] == 'LOT_SIZE', symbol_info['filters']), None)
        if not lot_size_filter:
            log_to_file(f"Brak filtru LOT_SIZE dla symbolu {symbol}.")
            return 0.0

        min_qty = float(lot_size_filter['minQty'])
        step_size = float(lot_size_filter['stepSize'])

        # Oblicz kwotę transakcji
        trade_amount_usdt = available_balance * (percentage / 100)

        # Pobierz aktualną cenę
        ticker = client.get_symbol_ticker(symbol=symbol)
        current_price = float(ticker['price'])

        # Oblicz ilość w jednostkach waluty bazowej
        quantity = trade_amount_usdt / current_price

        # Dostosuj ilość do wymagań LOT_SIZE
        quantity = max(min_qty, quantity)
        quantity = round(quantity // step_size * step_size, 8)

        return quantity

    except Exception as e:
        log_to_file(f"Błąd podczas obliczania kwoty transakcji: {e}")
        return 0.0

def has_open_position(symbol):
    """
    Sprawdza, czy istnieje otwarta pozycja dla danego symbolu.
    """
    try:
        open_positions = client.get_open_orders(symbol=symbol)
        for order in open_positions:
            if order['type'] == 'MARKET' and order['status'] in ['NEW', 'PARTIALLY_FILLED', 'FILLED']:
                return True
        return False
    except Exception as e:
        log_to_file(f"Błąd podczas sprawdzania otwartych pozycji dla {symbol}: {e}")
        return False

def get_executed_price(order):
    """
    Pobiera rzeczywistą cenę wykonania zlecenia market.
    """
    if 'fills' in order and len(order['fills']) > 0:
        return float(order['fills'][0]['price'])
    return 0.0

def log_order(order, order_type, symbol, quantity, price):
    """
    Loguje zlecenie w pliku logfile.txt.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = (
        f"[{timestamp}] Zlecenie {order_type} dla {symbol}: "
        f"ID={order['orderId']}, Ilość={quantity}, Cena={price}, "
    )
    log_to_file(log_message)

def fit_price_to_filter(symbol: str, cena: float) -> float:
    """
    Zapewnia zdgodność z filtrami binance
    """
    # Pobierz informacje o symbolu
    symbol_info = client.get_symbol_info(symbol)
    
    # Znajdź filtr PRICE_FILTER
    price_filter = next(filter(lambda x: x['filterType'] == 'PRICE_FILTER', symbol_info['filters']))
    
    tick_size = float(price_filter['tickSize'])
    min_price = float(price_filter['minPrice'])
    max_price = float(price_filter['maxPrice'])
    
    # Zaokrąglij cenę do najbliższej wartości zgodnej z tickSize
    corrected_price = round(cena / tick_size) * tick_size
    
    # Upewnij się, że cena jest w dozwolonym zakresie
    corrected_price = max(min_price, min(max_price, corrected_price))
    
    return corrected_price


def execute_trade(signal, percentage=20):
    """
    Wykonuje transakcję na Binance na podstawie sygnału.
    """
    symbol = signal["currency"]

    # Sprawdź, czy istnieje już otwarta pozycja dla tego symbolu
    if has_open_position(symbol):
        log_to_file(f"Otwarta pozycja dla {symbol} już istnieje. Pomijanie nowego zlecenia.")
        return

    # Pobierz dostępne środki
    available_balance = get_available_balance("USDT")
    if available_balance <= 0:
        print("Brak dostępnych środków.")
        return

    # Oblicz ilość w jednostkach waluty bazowej
    quantity = calculate_trade_amount(available_balance, percentage, symbol)
    if quantity <= 0:
        log_to_file(f"Nie można obliczyć ilości dla {symbol}.")
        return

    side = SIDE_SELL if signal["signal_type"] == "SHORT" else SIDE_BUY
    num_targets = len(signal["targets"])
    quantity_per_target = quantity / num_targets

    # Wykonaj zlecenie marketowe
    try:
        log_to_file(f"Rozpoczynam składanie zlecenia MARKET dla {symbol}: ilość={quantity}, side={side}")
        order = client.create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )
        executed_price = get_executed_price(order)
        print(f"Zlecenie marketowe wykonane: {order}")
        log_order(order, "MARKET", symbol, quantity, executed_price)

        if "orders" not in signal:
            signal["orders"] = []
        signal["orders"].append({
            "orderId": order['orderId'],
            "type": "MARKET",
            "status": order['status'],
            "stopPrice": executed_price,
            "side": order['side'],
            "quantity": float(order['origQty']),
            "executedQty": float(order['executedQty']),
            "time": order['transactTime']
        })
        log_to_file(f"Otwarto nową pozycję: {order['orderId']}")

        # Oczekuj na wypełnienie zlecenia marketowego
        while True:
            try:
                time.sleep(2)  # Czekaj 2 sekundy przed sprawdzeniem statusu zlecenia
                params = {
                    'symbol': symbol,
                    'orderId': order['orderId']
                }
                order_status = client.get_order(**params)
                
                if order_status['status'] == 'FILLED':
                    log_to_file(f"Wypełnienie zlecenia marketowego {order['orderId']}...")
                    break
                log_to_file(f"Oczekiwanie na wypełnienie zlecenia marketowego {order['orderId']}...")
                time.sleep(1)  # Czekaj 1 sekundę przed kolejnym sprawdzeniem
            except Exception as e:
                log_to_file(f"Błąd podczas sprawdzania statusu zlecenia {order['orderId']}: {str(e)}")
                log_to_file(f"Pełny kontekst błędu: {e.__dict__}")  # Logowanie pełnego kontekstu błędu
                time.sleep(1)  # Poczekaj i spróbuj ponownie

        # Ustaw zlecenie stop-loss
        try:
            price_corrected_to_filters = fit_price_to_filter(symbol, signal["stop_loss"]) #zapewnia zdgodność z filtrami binance
            log_to_file(f"Rozpoczynam składanie zlecenia STOP_LOSS dla {symbol}: ilość={quantity}, stopPrice={price_corrected_to_filters}")
            stop_loss_order = client.create_order(
                symbol=symbol,
                side=SIDE_BUY if side == SIDE_SELL else SIDE_SELL,
                type="STOP_LOSS",  # Używamy STOP_LOSS
                quantity=quantity,
                stopPrice=price_corrected_to_filters
            )
            print(f"Zlecenie stop-loss wykonane: {stop_loss_order}")
            params = {
                    'symbol': symbol,
                    'orderId': stop_loss_order['orderId']
                }
            stop_loss_order = client.get_order(**params)
            
            log_order(stop_loss_order, "STOP_LOSS", symbol, quantity, signal["stop_loss"])
            # Dodaj zlecenie stop-loss do historii sygnału
            if "orders" not in signal:
                signal["orders"] = []
            signal["orders"].append({
                "orderId": stop_loss_order['orderId'],
                "type": "STOP_LOSS",
                "status": "NEW",
                "stopPrice": price_corrected_to_filters,
                "side": stop_loss_order['side'],
                "quantity": float(stop_loss_order['origQty']),
                "executedQty": float(stop_loss_order['executedQty']),
                "time": stop_loss_order['transactTime']
            })
        except Exception as e:
            log_to_file(f"Błąd podczas składania zlecenia stop-loss: {str(e)}")
            log_to_file(f"Pełny kontekst błędu: {e.__dict__}")  # Logowanie pełnego kontekstu błędu
            # Jeśli zlecenie stop-loss się nie uda, anuluj zlecenie marketowe (jeśli jeszcze istnieje)

        # Ustaw zlecenia take-profit
        for i, target in enumerate(signal["targets"]):
            try:
                log_to_file(f"Rozpoczynam składanie zlecenia TAKE_PROFIT_LIMIT {i + 1} dla {symbol}: ilość={quantity_per_target}, stopPrice={target}, price={target}")
                target_corrected_to_filers = fit_price_to_filter(symbol, target) #zapewnia zdgodność z filtrami binance
                take_profit_order = client.create_order(
                    symbol=symbol,
                    side=SIDE_BUY if side == SIDE_SELL else SIDE_SELL,
                    type="TAKE_PROFIT",  # Używamy TAKE_PROFIT_LIMIT
                    quantity=quantity_per_target,
                    stopPrice=target_corrected_to_filers,
                )
                print(f"Zlecenie take-profit {i + 1} wykonane: {take_profit_order}")
                log_order(take_profit_order, "TAKE_PROFIT_LIMIT", symbol, quantity_per_target, target_corrected_to_filers)
                # Dodaj zlecenie take-profit do historii sygnału
                signal["orders"].append({
                    "orderId": take_profit_order['orderId'],
                    "type": "TAKE_PROFIT",
                    "status": "NEW",
                    "stopPrice": target_corrected_to_filers,
                    "side": take_profit_order['side'],
                    "quantity": float(take_profit_order['origQty']),
                    "executedQty": float(take_profit_order['executedQty']),
                    "time": take_profit_order['transactTime']
                })
            except Exception as e:
                log_to_file(f"Błąd podczas składania zlecenia take-profit {i + 1}: {str(e)}")
                log_to_file(f"Pełny kontekst błędu: {e.__dict__}")  # Logowanie pełnego kontekstu błędu
                # Jeśli zlecenie take-profit się nie uda, anuluj pozostałe zlecenia take-profit i stop-loss.
                for order in signal["orders"]:
                    if order["type"] in ["TAKE_PROFIT", "STOP_LOSS"]:
                        try:
                            client.cancel_order(symbol=symbol, orderId=order['orderId'])
                            log_to_file(f"Anulowano zlecenie {order['type']} {order['orderId']} z powodu błędu.")
                        except Exception as e:
                            log_to_file(f"Błąd podczas anulowania zlecenia {order['type']} {order['orderId']}: {str(e)}")
                            log_to_file(f"Pełny kontekst błędu: {e.__dict__}")  # Logowanie pełnego kontekstu błędu
                return

    except Exception as e:
        print(f"Błąd podczas wykonywania transakcji: {str(e)}")
        log_to_file(f"Błąd podczas wykonywania transakcji: {str(e)}")
        log_to_file(f"Pełny kontekst błędu: {e.__dict__}")  # Logowanie pełnego kontekstu błędu
        
        