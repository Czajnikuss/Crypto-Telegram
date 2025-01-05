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
        f"Status={order['status']}"
    )
    log_to_file(log_message)

import time

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

        # Dodaj pozycję do listy otwartych pozycji
        open_positions[order['orderId']] = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "status": "OPEN",
            "executed_price": executed_price
        }
        log_to_file(f"Otwarto nową pozycję: {order['orderId']}")

        # Oczekuj na wypełnienie zlecenia marketowego
        while True:
            try:
                time.sleep(2)  # Czekaj 2 sekundy przed sprawdzeniem statusu zlecenia
                order_status = client.get_order(symbol=symbol, orderId=order['orderId'])
                if order_status['status'] == 'FILLED':
                    break
                log_to_file(f"Oczekiwanie na wypełnienie zlecenia marketowego {order['orderId']}...")
                time.sleep(1)  # Czekaj 1 sekundę przed kolejnym sprawdzeniem
            except Exception as e:
                log_to_file(f"Błąd podczas sprawdzania statusu zlecenia {order['orderId']}: {str(e)}")
                log_to_file(f"Pełny kontekst błędu: {e.__dict__}")  # Logowanie pełnego kontekstu błędu
                time.sleep(1)  # Poczekaj i spróbuj ponownie

        # Ustaw zlecenie stop-loss
        try:
            log_to_file(f"Rozpoczynam składanie zlecenia STOP_LOSS dla {symbol}: ilość={quantity}, stopPrice={signal['stop_loss']}")
            stop_loss_order = client.create_order(
                symbol=symbol,
                side=SIDE_BUY if side == SIDE_SELL else SIDE_SELL,
                type="STOP_LOSS",  # Używamy STOP_LOSS
                quantity=quantity,
                stopPrice=signal["stop_loss"]
            )
            print(f"Zlecenie stop-loss wykonane: {stop_loss_order}")
            log_order(stop_loss_order, "STOP_LOSS", symbol, quantity, signal["stop_loss"])
            # Dodaj zlecenie stop-loss do historii sygnału
            signal["orders"].append({
                "orderId": stop_loss_order['orderId'],
                "type": "STOP_LOSS",
                "status": stop_loss_order['status'],
                "stopPrice": float(signal["stop_loss"]),
                "side": stop_loss_order['side'],
                "quantity": float(stop_loss_order['origQty']),
                "executedQty": float(stop_loss_order['executedQty']),
                "time": stop_loss_order['transactTime']
            })
        except Exception as e:
            log_to_file(f"Błąd podczas składania zlecenia stop-loss: {str(e)}")
            log_to_file(f"Pełny kontekst błędu: {e.__dict__}")  # Logowanie pełnego kontekstu błędu
            # Jeśli zlecenie stop-loss się nie uda, anuluj zlecenie marketowe (jeśli jeszcze istnieje)
            try:
                time.sleep(2)  # Czekaj 2 sekundy przed sprawdzeniem statusu zlecenia
                order_status = client.get_order(symbol=symbol, orderId=order['orderId'])
                if order_status['status'] in ['NEW', 'PARTIALLY_FILLED']:
                    client.cancel_order(symbol=symbol, orderId=order['orderId'])
                    log_to_file(f"Anulowano zlecenie marketowe {order['orderId']} z powodu błędu stop-loss.")
            except Exception as e:
                log_to_file(f"Błąd podczas anulowania zlecenia marketowego {order['orderId']}: {str(e)}")
                log_to_file(f"Pełny kontekst błędu: {e.__dict__}")  # Logowanie pełnego kontekstu błędu
            return

        # Ustaw zlecenia take-profit
        for i, target in enumerate(signal["targets"]):
            try:
                log_to_file(f"Rozpoczynam składanie zlecenia TAKE_PROFIT_LIMIT {i + 1} dla {symbol}: ilość={quantity_per_target}, stopPrice={target}, price={target}")
                take_profit_order = client.create_order(
                    symbol=symbol,
                    side=SIDE_BUY if side == SIDE_SELL else SIDE_SELL,
                    type="TAKE_PROFIT_LIMIT",  # Używamy TAKE_PROFIT_LIMIT
                    quantity=quantity_per_target,
                    stopPrice=target,
                    price=target,  # Cena wykonania
                    timeInForce="GTC"  # Good Till Cancel
                )
                print(f"Zlecenie take-profit {i + 1} wykonane: {take_profit_order}")
                log_order(take_profit_order, "TAKE_PROFIT_LIMIT", symbol, quantity_per_target, target)
                # Dodaj zlecenie take-profit do historii sygnału
                signal["orders"].append({
                    "orderId": take_profit_order['orderId'],
                    "type": "TAKE_PROFIT_LIMIT",
                    "status": take_profit_order['status'],
                    "stopPrice": float(target),
                    "side": take_profit_order['side'],
                    "quantity": float(take_profit_order['origQty']),
                    "executedQty": float(take_profit_order['executedQty']),
                    "time": take_profit_order['transactTime']
                })
            except Exception as e:
                log_to_file(f"Błąd podczas składania zlecenia take-profit {i + 1}: {str(e)}")
                log_to_file(f"Pełny kontekst błędu: {e.__dict__}")  # Logowanie pełnego kontekstu błędu
                # Jeśli zlecenie take-profit się nie uda, anuluj pozostałe zlecenia take-profit i stop-loss
                for order in signal["orders"]:
                    if order["type"] in ["TAKE_PROFIT_LIMIT", "STOP_LOSS"]:
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
        
        
def check_open_positions():
    """
    Sprawdza status otwartych pozycji i loguje zmiany.
    """
    for order_id, position in list(open_positions.items()):
        try:
            order_status = client.get_order(symbol=position["symbol"], orderId=order_id)
            if order_status['status'] != position["status"]:
                log_to_file(f"Zmiana statusu pozycji {order_id}: {position['status']} -> {order_status['status']}")
                position["status"] = order_status['status']

                # Jeśli pozycja została zamknięta, usuń ją z listy
                if order_status['status'] in ["FILLED", "CANCELED", "EXPIRED"]:
                    del open_positions[order_id]
                    log_to_file(f"Pozycja {order_id} została zamknięta.")
                    # Pobierz stan konta po zamknięciu pozycji
                    balance_after = get_available_balance("USDT")
                    log_to_file(f"Stan konta po zamknięciu pozycji: {balance_after}")

        except Exception as e:
            print(f"Błąd podczas sprawdzania statusu pozycji {order_id}: {e}")