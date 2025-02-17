import json
import os
import time
from common import client, log_to_file, adjust_price, adjust_quantity, get_order_details, create_oco_order_direct, get_order_reports, get_all_oco_orders_for_symbol
# Dodano importy
from binance.exceptions import BinanceAPIException
import traceback

SIGNAL_HISTORY_FILE = 'signal_history.json'

def load_signal_history():
    if os.path.exists(SIGNAL_HISTORY_FILE):
        with open(SIGNAL_HISTORY_FILE, 'r') as file:
            return json.load(file)
    return []

def save_signal_history(history):
    with open(SIGNAL_HISTORY_FILE, 'w') as file:
        json.dump(history, file, indent=4)

def handle_critical_error(signal):
    symbol = signal["currency"]
    try:
        # Sprawdź czy jest co zamykać
        account = client.get_account()
        symbol_info = client.get_symbol_info(symbol)
        base_asset = symbol_info['baseAsset']
        base_balance = float(next((b['free'] for b in account['balances'] if b['asset'] == base_asset), 0))

        if base_balance > 0:
            closing_side = 'SELL' if signal['signal_type'] == 'LONG' else 'BUY'
            adjusted_quantity = adjust_quantity(symbol, base_balance)

            if adjusted_quantity > 0:
                client.create_order(
                    symbol=symbol,
                    side=closing_side,
                    type='MARKET',
                    quantity=adjusted_quantity
                )
                log_to_file(f"Awaryjne zamknięcie pozycji dla {symbol}, ilość: {adjusted_quantity}")

        signal["status"] = "CLOSED"
        signal["error"] = "CRITICAL_ERROR"

    except Exception as e:
        log_to_file(f"Błąd podczas obsługi sytuacji krytycznej dla {symbol}: {e}")

def update_signal_high_price(signal, current_price):
    """Aktualizuje najwyższą osiągniętą cenę w sygnale"""
    is_long = signal['signal_type'] == 'LONG'
    current_high = signal.get('highest_price', current_price if is_long else float('inf'))

    if is_long:
        signal['highest_price'] = max(current_high, current_price)
    else:
        signal['highest_price'] = min(current_high, current_price)

    return signal

def get_base_balance(symbol):
    """Pobiera saldo dla danej pary tradingowej"""
    symbol_info = client.get_symbol_info(symbol)
    base_asset = symbol_info['baseAsset']
    account = client.get_account()
    return float(next(
        (b['free'] for b in account['balances'] if b['asset'] == base_asset),
        0
    ))

def check_and_update_signal_history():
    from binance_trading import add_order_to_history
    history = load_signal_history()

    for signal in history:
        if signal.get("status") == "CLOSED":
            continue

        try:
            symbol = signal["currency"]
            current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
            base_balance = get_base_balance(symbol)
            open_orders = client.get_open_orders(symbol=symbol) # Pobieramy otwarte zlecenia
            signal = update_signal_high_price(signal, current_price)

            # Sprawdź aktywną grupę OCO
            # Poprawione wyszukiwanie aktywnego OCO (sprawdzamy listStatusType)
            active_oco = next((oco for oco in (get_all_oco_orders_for_symbol(client, symbol, only_active=True) or [])
                                 if oco['orderListId'] == signal.get("oco_order_id")), None)

            # Obsługa błędów związanych z nieaktualnymi zleceniami
            try:
                # Jeśli brak OCO ale jest pozycja - utwórz nowe
                if not active_oco and base_balance > 0:
                    # Anuluj wszystkie istniejące zlecenia (tylko STOP_LOSS_LIMIT i LIMIT_MAKER)
                    for order in open_orders:
                        if order['type'] in ['STOP_LOSS_LIMIT', 'LIMIT_MAKER']:
                            try:
                                client.cancel_order(symbol=symbol, orderId=order['orderId'])
                                log_to_file(f"Anulowano zlecenie {order['orderId']} przed utworzeniem nowego OCO")
                            except BinanceAPIException as cancel_error:
                                if 'Unknown order sent' in str(cancel_error):
                                    log_to_file(f"Zlecenie {order['orderId']} już nie istnieje, pomijam")
                                    continue
                                else:
                                    log_to_file(f"Błąd anulowania zlecenia: {cancel_error}")
                                    raise # Rzuć wyjątek dalej, żeby obsłużyć go na wyższym poziomie

                    # Utwórz nowe OCO z pełnym balansem
                    entry_price = float(signal['real_entry'])
                    stop_loss, take_profit = calculate_oco_levels(signal, entry_price)

                    # Upewnij się, że ceny są poprawne dla PRICE_FILTER
                    stop_loss = adjust_price(symbol, stop_loss)
                    take_profit = adjust_price(symbol, take_profit)
                    base_balance = adjust_quantity(symbol, base_balance)  # Dostosuj ilość

                    oco_order = create_oco_order_direct(
                        client=client,
                        symbol=symbol,
                        side='SELL' if signal['signal_type'] == 'LONG' else 'BUY',
                        quantity=base_balance,
                        take_profit_price=take_profit,
                        stop_price=stop_loss,
                        stop_limit_price=stop_loss
                    )

                    if oco_order:
                        signal['oco_order_id'] = oco_order['orderListId']  # Zapisz ID grupy OCO
                        add_order_to_history(signal, oco_order, "OCO")
                        log_to_file(f"Utworzono nowe OCO dla {symbol}: SL={stop_loss}, TP={take_profit}, orderListId={oco_order['orderListId']}")

                # Aktualizacja statusu OCO
                if active_oco:
                    # Pobieramy statusy z *nowej* funkcji
                    order_reports = get_order_reports(client, active_oco['orderListId'], symbol)

                    # Sprawdzamy, czy którekolwiek ze zleceń w OCO zostało zrealizowane lub anulowane
                    for report in order_reports:
                        if report['status'] in ['FILLED', 'CANCELED']:
                            # Znajdź zrealizowane zlecenie (jeśli istnieje)
                            filled_order = next((r for r in order_reports if r['status'] == 'FILLED'), None)

                            if filled_order:
                                signal.update({
                                    "status": "CLOSED",
                                    "exit_price": filled_order['price'],  # Użyj ceny z raportu zlecenia
                                    "exit_time": filled_order['time'],
                                    "exit_type": "TAKE_PROFIT" if filled_order['type'] == 'LIMIT_MAKER' else "STOP_LOSS"
                                })
                                log_to_file(f"OCO zostało zrealizowane.  Typ: {signal['exit_type']}, Cena: {signal['exit_price']}")
                                break # Wyjdź z pętli po znalezieniu zrealizowanego zlecenia

            except Exception as main_error:
                log_to_file(f"Krytyczny błąd zarządzania OCO: {main_error}")
                log_to_file(traceback.format_exc())
                # handle_critical_error(signal)

            # Dynamiczna aktualizacja stop loss po osiągnięciu targetu
            if handle_targets(signal, current_price, base_balance):
                signal["status"] = "CLOSED"

        except Exception as e:
            log_to_file(f"Błąd podczas przetwarzania {symbol}: {e}")
            log_to_file(traceback.format_exc())
            # handle_critical_error(signal)

    save_signal_history(history)


def calculate_oco_levels(signal, entry_price):
    """Oblicza poziomy dla OCO na podstawie targetów i aktualnej ceny"""
    targets = signal["targets"]
    highest_price = signal.get('highest_price', entry_price)
    is_long = signal['signal_type'] == 'LONG'

    # Stop loss dynamiczny
    if any(highest_price >= t if is_long else highest_price <= t for t in targets):
        stop_loss = entry_price
    else:
        stop_loss = signal['stop_loss']

    # Take profit zawsze na ostatni target
    take_profit = targets[-1]

    return stop_loss, take_profit  # NIE UŻYWAJ adjust_price TUTAJ!


def handle_targets(signal, current_price, base_balance):
    from binance_trading import add_order_to_history
    """Aktualizuje OCO przy osiągnięciu targetu 1"""
    is_long = signal['signal_type'] == 'LONG'
    targets = signal["targets"]

    if len(targets) < 2:
        return False

    # Sprawdź czy osiągnięto pierwszy target
    target1_reached = (current_price >= targets[0] if is_long else current_price <= targets[0])

    if target1_reached and not signal.get('target1_activated'):
        try:
            symbol = signal["currency"]
            open_orders = client.get_open_orders(symbol=symbol)

            # Anuluj istniejące OCO (tylko STOP_LOSS_LIMIT i LIMIT_MAKER)
            for order in open_orders:
                if order['type'] in ['STOP_LOSS_LIMIT', 'LIMIT_MAKER']:
                    try:
                        client.cancel_order(symbol=symbol, orderId=order['orderId'])
                        log_to_file(f"Anulowano zlecenie {order['orderId']} po osiągnięciu targetu 1")
                    except BinanceAPIException as cancel_error:
                        if 'Unknown order sent' in str(cancel_error):
                            log_to_file(f"Zlecenie {order['orderId']} już nie istnieje, pomijam")
                            continue
                        else:
                            log_to_file(f"Błąd anulowania zlecenia: {cancel_error}")
                            raise

            # Utwórz nowe OCO ze stop loss na entry price
            entry_price = float(signal['real_entry'])
            new_stop_loss = entry_price
            take_profit = targets[-1]

            # Upewnij się, że ceny są poprawne dla PRICE_FILTER
            new_stop_loss = adjust_price(symbol, new_stop_loss)
            take_profit = adjust_price(symbol, take_profit)
            base_balance = adjust_quantity(symbol, base_balance)  # Dostosuj ilość

            oco_order = create_oco_order_direct(
                client=client,
                symbol=symbol,
                side='SELL' if is_long else 'BUY',
                quantity=base_balance,
                take_profit_price=take_profit,
                stop_price=new_stop_loss,
                stop_limit_price=new_stop_loss
            )

            if oco_order:
                signal['target1_activated'] = True
                signal['oco_order_id'] = oco_order['orderListId']  # Zapisz ID grupy OCO
                add_order_to_history(signal, oco_order, "OCO")
                log_to_file(f"Zaktualizowano OCO po osiągnięciu targetu 1: SL={new_stop_loss}, orderListId={oco_order['orderListId']}")

        except Exception as e:
            log_to_file(f"Błąd aktualizacji OCO: {e}")
            log_to_file(traceback.format_exc())

    return False

def find_oco_order(open_orders, oco_order_id):
    """Znajduje aktywne zlecenie OCO po ID"""
    if not oco_order_id:
        return None
    return next((order for order in open_orders if order.get("orderListId") == oco_order_id), None)

def find_executed_oco_order(order_history, oco_order_id):
    """Znajduje wykonane zlecenie OCO w historii"""
    if not oco_order_id:
        return None
    executed_orders = [order for order in order_history
                      if order.get("orderListId") == oco_order_id
                      and order["status"] == "FILLED"]
    return executed_orders[0] if executed_orders else None
