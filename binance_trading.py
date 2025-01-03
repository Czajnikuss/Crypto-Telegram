from binance.client import Client
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET, ORDER_TYPE_TAKE_PROFIT, ORDER_TYPE_STOP_LOSS
from dotenv import load_dotenv
import os
import logging
from datetime import datetime

def log_to_file(message):
    """
    Zapisuje wiadomość do pliku logfile.txt z timestampem.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("logfile.txt", "a", encoding="utf-8") as log_file:  # Dodaj encoding="utf-8"
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

# Funkcja do pobierania dostępnych środków
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

# Funkcja do obliczania kwoty transakcji
def calculate_trade_amount(available_balance, percentage):
    """
    Oblicza kwotę transakcji jako procent dostępnych środków.
    """
    return available_balance * (percentage / 100)

def get_account_balance():
    """
    Pobiera stan konta dla wszystkich walut.
    """
    try:
        account_info = client.get_account()
        balances = account_info['balances']
        return {balance['asset']: float(balance['free']) for balance in balances if float(balance['free']) > 0}
    except Exception as e:
        print(f"Błąd podczas pobierania salda: {e}")
        return {}
    
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
    
def update_stop_loss_if_needed(signal):
    """
    Aktualizuje stop-loss tylko wtedy, gdy jest to konieczne.
    """
    symbol = signal["currency"]
    stop_loss_order = next((order for order in signal["orders"] if order['type'] == 'STOP_LOSS'), None)
    if not stop_loss_order:
        return

    # Sprawdź, czy zlecenie nadal istnieje
    try:
        order_status = client.get_order(symbol=symbol, orderId=stop_loss_order['orderId'])
        if order_status['status'] not in ['NEW', 'PARTIALLY_FILLED']:
            log_to_file(f"Zlecenie stop-loss {stop_loss_order['orderId']} już nie istnieje.")
            return
    except Exception as e:
        log_to_file(f"Błąd podczas sprawdzania statusu zlecenia stop-loss: {e}")
        return

    # Określ, na którym celu jesteśmy
    current_target_index = len(signal["achieved_targets"])

    # Określ nowy poziom stop-loss
    if current_target_index == 0:
        new_stop_loss = signal["stop_loss"]  # Oryginalny stop-loss
    elif current_target_index == 1:
        new_stop_loss = signal["entry"]  # Poziom wejścia
    else:
        new_stop_loss = signal["targets"][current_target_index - 2]  # Poprzedni cel

    # Sprawdź, czy stop-loss jest już ustawiony na odpowiednim poziomie
    if (signal["signal_type"] == "LONG" and stop_loss_order['stopPrice'] >= new_stop_loss) or \
       (signal["signal_type"] == "SHORT" and stop_loss_order['stopPrice'] <= new_stop_loss):
        return  # Stop-loss jest już ustawiony prawidłowo

    # Zaktualizuj stop-loss
    try:
        client.cancel_order(symbol=symbol, orderId=stop_loss_order['orderId'])
        new_order = client.create_order(
            symbol=symbol,
            side=stop_loss_order['side'],
            type='STOP_LOSS',
            quantity=stop_loss_order['quantity'],
            stopPrice=new_stop_loss
        )
        log_to_file(f"Zaktualizowano stop-loss dla {symbol} na {new_stop_loss}.")
        # Zaktualizuj zlecenie w historii
        stop_loss_order['stopPrice'] = new_stop_loss
        stop_loss_order['orderId'] = new_order['orderId']
    except Exception as e:
        log_to_file(f"Błąd podczas aktualizacji stop-loss dla {symbol}: {e}")

def adjust_targets_based_on_limit(signal):
    """
    Dostosowuje liczbę celów, aby zmieścić się w limicie zleceń algorytmicznych.
    """
    max_algo_orders = 5  # Limit Binance
    current_algo_orders = get_algo_orders_count(signal["currency"])

    # Oblicz dostępną liczbę celów
    available_targets = max_algo_orders - current_algo_orders - 1  # -1 dla STOP_LOSS

    if available_targets < 0:
        available_targets = 0  # Nie możemy mieć ujemnej liczby celów

    # Ogranicz liczbę celów
    if len(signal["targets"]) > available_targets:
        log_to_file(f"Przekroczono limit zleceń. Dostępne cele: {available_targets}")
        signal["targets"] = signal["targets"][:available_targets]  # Zostaw najniższe cele

    return signal

def execute_trade(signal, percentage=20):
    """
    Wykonuje transakcję na Binance na podstawie sygnału.
    """
    symbol = signal["currency"]

    # Dostosuj liczbę celów do wymagań LOT_SIZE
    signal = adjust_targets_based_on_lot_size(signal)

    # Reszta kodu funkcji execute_trade pozostaje bez zmian
    base_asset = symbol.replace("USDT", "")
    quote_asset = "USDT"

    # Pobierz stan konta przed zleceniem
    balance_before = get_account_balance()
    log_to_file(f"Stan konta przed zleceniem: {balance_before}")

    # Pobierz dostępne środki
    available_balance = get_available_balance(quote_asset)
    if available_balance <= 0:
        print("Brak dostępnych środków.")
        return

    # Oblicz kwotę transakcji
    trade_amount_usdt = calculate_trade_amount(available_balance, percentage)
    print(f"Dostępne środki: {available_balance} {quote_asset}")
    print(f"Kwota transakcji ({percentage}%): {trade_amount_usdt} {quote_asset}")

    # Pobierz aktualną cenę
    ticker = client.get_symbol_ticker(symbol=symbol)
    current_price = float(ticker['price'])

    # Oblicz ilość w jednostkach waluty bazowej
    quantity = trade_amount_usdt / current_price

    # Sprawdź wymagania LOT_SIZE
    symbol_info = client.get_symbol_info(symbol)
    lot_size_filter = next(filter(lambda f: f['filterType'] == 'LOT_SIZE', symbol_info['filters']))
    min_qty = float(lot_size_filter['minQty'])
    step_size = float(lot_size_filter['stepSize'])

    # Dostosuj ilość do wymagań LOT_SIZE
    quantity = max(min_qty, quantity)
    quantity = round(quantity // step_size * step_size, 8)

    side = SIDE_SELL if signal["signal_type"] == "SHORT" else SIDE_BUY
    num_targets = len(signal["targets"])
    quantity_per_target = quantity / num_targets

    # Wykonaj zlecenie marketowe
    try:
        order = client.create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )
        print(f"Zlecenie marketowe wykonane: {order}")

        # Dodaj pozycję do listy otwartych pozycji
        open_positions[order['orderId']] = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "status": "OPEN"
        }
        log_to_file(f"Otwarto nową pozycję: {order['orderId']}")

        # Ustaw zlecenie stop-loss
        stop_loss_order = client.create_order(
            symbol=symbol,
            side=SIDE_BUY if side == SIDE_SELL else SIDE_SELL,
            type=ORDER_TYPE_STOP_LOSS,
            quantity=quantity,
            stopPrice=signal["stop_loss"]
        )
        print(f"Zlecenie stop-loss wykonane: {stop_loss_order}")

        # Ustaw zlecenia take-profit
        for i, target in enumerate(signal["targets"]):
            take_profit_order = client.create_order(
                symbol=symbol,
                side=SIDE_BUY if side == SIDE_SELL else SIDE_SELL,
                type=ORDER_TYPE_TAKE_PROFIT,
                quantity=quantity_per_target,
                stopPrice=target
            )
            print(f"Zlecenie take-profit {i + 1} wykonane: {take_profit_order}")

        # Pobierz stan konta po zleceniu
        balance_after = get_account_balance()
        log_to_file(f"Stan konta po zleceniu: {balance_after}")

    except Exception as e:
        print(f"Błąd podczas wykonywania transakcji: {e}")
        log_to_file(f"Błąd podczas wykonywania transakcji: {e}")

def adjust_targets_based_on_lot_size(signal):
    """
    Dostosowuje liczbę celów, aby zmieścić się w wymaganiach LOT_SIZE.
    """
    symbol = signal["currency"]
    try:
        symbol_info = client.get_symbol_info(symbol)
        if not symbol_info:
            log_to_file(f"Nie można pobrać informacji o symbolu {symbol}.")
            return signal

        lot_size_filter = next(filter(lambda f: f['filterType'] == 'LOT_SIZE', symbol_info['filters']), None)
        if not lot_size_filter:
            log_to_file(f"Brak filtru LOT_SIZE dla symbolu {symbol}.")
            return signal

        min_qty = float(lot_size_filter['minQty'])
        step_size = float(lot_size_filter['stepSize'])

        # Oblicz minimalną ilość dla każdego celu
        min_target_qty = min_qty * len(signal["targets"])

        # Sprawdź, czy ilość spełnia wymagania LOT_SIZE
        if min_target_qty > step_size:
            # Ogranicz liczbę celów, aby zmieścić się w wymaganiach LOT_SIZE
            max_targets = int(step_size // min_qty)
            if max_targets < len(signal["targets"]):
                log_to_file(f"Przekroczono limit LOT_SIZE. Dostępne cele: {max_targets}")
                signal["targets"] = signal["targets"][:max_targets]
                signal["modifications"] = signal.get("modifications", [])
                signal["modifications"].append({
                    "type": "LOT_SIZE_ADJUSTMENT",
                    "reason": f"Minimalny rozmiar lotu: {min_qty}, step size: {step_size}",
                    "new_targets": signal["targets"]
                })

    except Exception as e:
        log_to_file(f"Błąd podczas dostosowywania celów do LOT_SIZE: {e}")

    return signal


def check_open_positions():
    """
    Sprawdza status otwartych pozycji i loguje zmiany.
    """
    for order_id, position in list(open_positions.items()):  # Użyj listy do iteracji
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
                    balance_after = get_account_balance()
                    log_to_file(f"Stan konta po zamknięciu pozycji: {balance_after}")

        except Exception as e:
            print(f"Błąd podczas sprawdzania statusu pozycji {order_id}: {e}")