import json
import os
import time
from common import client, log_to_file, adjust_price, adjust_quantity, get_order_details, get_min_notional, create_oco_order_direct, get_order_reports, get_all_oco_orders_for_symbol
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
        history = load_signal_history()
        for i, s in enumerate(history):
            if s["currency"] == signal["currency"] and s["date"] == signal["date"]:
                history[i] = signal
                break
        save_signal_history(history)

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

def calculate_profit(signal, exit_price, exit_quantity):
    """Oblicza zysk lub stratę na podstawie danych z sygnału i zlecenia."""
    try:
        real_entry = float(signal['real_entry'])
        real_amount = float(signal['real_amount'])
        is_long = signal['signal_type'] == 'LONG'

        amount_diff = real_amount - exit_quantity
        if is_long:
            profit = (exit_price - real_entry) * exit_quantity
        else:
            profit = (real_entry - exit_price) * exit_quantity

        return profit, amount_diff

    except Exception as e:
        log_to_file(f"Błąd podczas obliczania zysku: {e}")
        return None, None

def update_signal_with_profit_info(signal, filled_order):
    """Aktualizuje sygnał o informacje o zysku/stracie."""
    try:
        exit_price = float(filled_order['price'])
        exit_quantity = float(filled_order['executedQty'])

        profit, amount_diff = calculate_profit(signal, exit_price, exit_quantity)

        if profit is not None:
            signal['exit_price'] = exit_price
            signal['exit_quantity'] = exit_quantity
            signal['real_gain'] = profit
            signal['amount_difference'] = amount_diff
            exit_type = "TAKE_PROFIT" if filled_order['type'] == 'LIMIT_MAKER' else "STOP_LOSS"
            signal['exit_type'] = exit_type
            profit_percentage = (profit / (float(signal['real_entry']) * exit_quantity)) * 100
            signal['status_description'] = f"{'Gain' if profit > 0 else 'Loss'}: {profit:.2f} - Amount: {exit_quantity:.4f} - Percentage: {profit_percentage:.2f}%"
            log_to_file(f"Sygnał zamknięty z zyskiem: {profit:.2f}, opis: {signal['status_description']}")
        else:
            log_to_file("Nie udało się obliczyć zysku/straty.")

    except Exception as e:
        log_to_file(f"Błąd podczas aktualizacji informacji o zysku w sygnale: {e}")

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
            open_orders = client.get_open_orders(symbol=symbol)
            signal = update_signal_high_price(signal, current_price)

            if 'current_target_level' not in signal:
                signal['current_target_level'] = 0

            active_oco = next((oco for oco in get_all_oco_orders_for_symbol(client, symbol, only_active=True)
                               if oco['orderListId'] == signal.get("oco_order_id")), None)

            min_notional = get_min_notional(symbol)

            if base_balance > 0 and base_balance * current_price < min_notional * 1.1:
                log_to_file(f"Mała kwota na koncie dla {symbol}: {base_balance} (wartość: {base_balance * current_price} < {min_notional * 1.1})")
                if "orders" in signal:
                    last_orders = signal["orders"]
                    all_filled_or_closed = all(
                        order["status"] in ["FILLED", "CANCELED", "EXPIRED"]
                        for order in last_orders if "oco_group_id" in order and order["oco_group_id"] == signal.get("oco_order_id")
                    )
                    if all_filled_or_closed:
                        filled_order = next((o for o in last_orders if o["status"] == "FILLED"), None)
                        if filled_order:
                            update_signal_with_profit_info(signal, filled_order)
                            signal.update({
                                "status": "CLOSED",
                                "exit_time": filled_order["time"],
                            })
                            log_to_file(f"Sygnał {symbol} zamknięty na podstawie historii: {signal['status_description']}")
                        else:
                            signal.update({
                                "status": "CLOSED",
                                "error": "RESIDUAL_BALANCE_CLOSED",
                                "status_description": f"Zamknięto z pozostałością {base_balance} bez zysku"
                            })
                            log_to_file(f"Sygnał {symbol} zamknięty z pozostałością {base_balance} bez wykonanych zleceń")
                        save_signal_history(history)
                        continue

            if not active_oco and base_balance > 0:
                notional_value = base_balance * current_price
                if notional_value < min_notional:
                    log_to_file(f"Pominięto tworzenie OCO dla {symbol}: wartość {notional_value} poniżej MIN_NOTIONAL {min_notional}")
                    signal["status"] = "CLOSED"
                    signal["error"] = f"RESIDUAL_AMOUNT_TOO_SMALL: {base_balance} ({notional_value} < {min_notional})"
                    save_signal_history(history)
                    continue

                for order in open_orders:
                    if order['type'] in ['STOP_LOSS_LIMIT', 'LIMIT_MAKER']:
                        try:
                            client.cancel_order(symbol=symbol, orderId=order['orderId'])
                            log_to_file(f"Anulowano zlecenie {order['orderId']} przed utworzeniem nowego OCO")
                        except BinanceAPIException as cancel_error:
                            if 'Unknown order sent' in str(cancel_error):
                                log_to_file(f"Zlecenie {order['orderId']} już nie istnieje, pomijam")
                            else:
                                log_to_file(f"Błąd anulowania zlecenia: {cancel_error}")
                                raise

                entry_price = float(signal['real_entry'])
                stop_loss, take_profit = calculate_oco_levels(signal, entry_price)
                if stop_loss is None or take_profit is None:
                    signal["status"] = "CLOSED"
                    signal["status_description"] = "All targets reached"
                    log_to_file(f"Sygnał {symbol} zamknięty - wszystkie targety osiągnięte")
                    continue

                stop_loss = adjust_price(symbol, stop_loss)
                take_profit = adjust_price(symbol, take_profit)
                adjusted_quantity = adjust_quantity(symbol, base_balance)

                adjusted_notional_tp = adjusted_quantity * take_profit
                adjusted_notional_sl = adjusted_quantity * stop_loss
                if adjusted_notional_tp < min_notional or adjusted_notional_sl < min_notional:
                    log_to_file(f"Po dostosowaniu ilości: wartość OCO dla {symbol} (TP: {adjusted_notional_tp}, SL: {adjusted_notional_sl}) poniżej MIN_NOTIONAL {min_notional}")
                    signal["status"] = "CLOSED"
                    signal["error"] = f"ADJUSTED_AMOUNT_TOO_SMALL: {adjusted_quantity} (TP: {adjusted_notional_tp}, SL: {adjusted_notional_sl} < {min_notional})"
                    save_signal_history(history)
                    continue

                oco_order = create_oco_order_direct(
                    client=client,
                    symbol=symbol,
                    side='SELL' if signal['signal_type'] == 'LONG' else 'BUY',
                    quantity=adjusted_quantity,
                    take_profit_price=take_profit,
                    stop_price=stop_loss,
                    stop_limit_price=stop_loss
                )

                if oco_order:
                    signal['oco_order_id'] = oco_order['orderListId']
                    add_order_to_history(signal, oco_order, "OCO")
                    log_to_file(f"Utworzono nowe OCO dla {symbol}: SL={stop_loss}, TP={take_profit}, orderListId={oco_order['orderListId']}")
                else:
                    log_to_file(f"Nie udało się utworzyć OCO dla {symbol} - pozostawiam sygnał otwarty z obecnym poziomem {signal['current_target_level']}")

            if active_oco:
                order_reports = get_order_reports(client, active_oco['orderListId'], symbol)
                filled_order = next((r for r in order_reports if r['status'] == 'FILLED'), None)
                is_any_expired_or_canceled = any(report['status'] in ['CANCELED', 'EXPIRED'] for report in order_reports)

                if filled_order:
                    update_signal_with_profit_info(signal, filled_order)
                    signal.update({
                        "status": "CLOSED",
                        "exit_time": filled_order['time'],
                    })
                    log_to_file(f"OCO zostało zrealizowane. Typ: {signal['exit_type']}, Cena: {signal['exit_price']}")
                elif is_any_expired_or_canceled and base_balance < (min_notional / current_price) * 0.1:  # Ulepszony warunek
                    filled_order = next((r for r in order_reports if r['status'] == 'FILLED'), None)
                    if filled_order:
                        update_signal_with_profit_info(signal, filled_order)
                        signal.update({
                            "status": "CLOSED",
                            "exit_time": filled_order['time'],
                        })
                        log_to_file(f"OCO zamknięte z jednym zleceniem wykonanym wcześniej. Typ: {signal['exit_type']}, Cena: {signal['exit_price']}")
                    else:
                        signal.update({
                            "status": "CLOSED",
                            "error": "OCO_EXPIRED_WITHOUT_FILL",
                            "status_description": "OCO wygasło bez pełnego wykonania"
                        })
                        log_to_file(f"OCO dla {symbol} wygasło bez realizacji pozycji")

            if handle_targets(signal, current_price, base_balance):
                signal["status"] = "CLOSED"

        except Exception as e:
            log_to_file(f"Błąd podczas przetwarzania {symbol}: {e}")
            log_to_file(traceback.format_exc())

    save_signal_history(history)

def validate_targets(signal):
    """Waliduje i sortuje targety w sygnale, usuwając nie-liczbowe wartości."""
    targets = signal.get("targets", [])
    is_long = signal['signal_type'] == 'LONG'
    
    # Filtruj tylko wartości liczbowe
    valid_targets = [t for t in targets if isinstance(t, (int, float)) and not isinstance(t, bool)]
    
    if not valid_targets:
        log_to_file(f"Sygnał {signal['currency']} nie ma ważnych targetów liczbowych")
        return []
    
    # Sortuj targety: rosnąco dla LONG, malejąco dla SHORT
    valid_targets.sort(reverse=not is_long)
    
    return valid_targets

def calculate_oco_levels(signal, entry_price):
    """Oblicza poziomy dla OCO na podstawie aktualnego poziomu targetu"""
    targets = validate_targets(signal)
    current_level = signal.get('current_target_level', 0)
    is_long = signal['signal_type'] == 'LONG'
    stop_loss_from_signal = signal.get("stop_loss")

    if not targets:
        log_to_file(f"Sygnał {signal['currency']} nie ma ważnych targetów - zwracam None")
        return None, None

    if current_level >= len(targets):
        log_to_file(f"Sygnał {signal['currency']} - wszystkie targety osiągnięte")
        return None, None  # Wszystkie targety osiągnięte

    # Oblicz stop_loss
    if current_level == 0:
        # Poziom 0: użyj stop_loss z sygnału, jeśli jest sensowny
        if stop_loss_from_signal is not None:
            if is_long and stop_loss_from_signal < entry_price:
                stop_loss = stop_loss_from_signal
            elif not is_long and stop_loss_from_signal > entry_price:
                stop_loss = stop_loss_from_signal
            else:
                stop_loss = entry_price * (0.85 if is_long else 1.15)  # Za duży SL, ustawiamy domyślnie
                log_to_file(f"Sygnał {signal['currency']} - stop_loss z sygnału ({stop_loss_from_signal}) niewłaściwy, ustawiono {stop_loss}")
        else:
            stop_loss = entry_price * (0.85 if is_long else 1.15)  # Brak SL w sygnale, domyślnie 0.85/1.15
            log_to_file(f"Sygnał {signal['currency']} - brak stop_loss w sygnale, ustawiono {stop_loss}")
    else:
        # Poziom 1+: stop_loss na entry_price (poziom 1) lub poprzednim targecie
        stop_loss = entry_price if current_level == 1 else targets[current_level - 2]

    # Take-profit na następnym targetcie (lub ostatnim, jeśli nie ma więcej)
    take_profit = targets[current_level] if current_level < len(targets) - 1 else targets[-1]

    # Walidacja relacji stop_loss i take_profit
    if is_long and (stop_loss >= entry_price or take_profit <= entry_price):
        log_to_file(f"Sygnał {signal['currency']} - nieprawidłowa relacja cen: SL={stop_loss}, TP={take_profit}, Entry={entry_price}")
        return None, None
    if not is_long and (stop_loss <= entry_price or take_profit >= entry_price):
        log_to_file(f"Sygnał {signal['currency']} - nieprawidłowa relacja cen: SL={stop_loss}, TP={take_profit}, Entry={entry_price}")
        return None, None

    return stop_loss, take_profit

def handle_targets(signal, current_price, base_balance):
    from binance_trading import add_order_to_history
    """Aktualizuje OCO przy osiągnięciu kolejnego targetu"""
    is_long = signal['signal_type'] == 'LONG'
    targets = signal["targets"]
    current_level = signal.get('current_target_level', 0)

    if not targets:  # Dodana walidacja pustej listy targets
        signal["status"] = "CLOSED"
        signal["error"] = "NO_TARGETS_DEFINED"
        log_to_file(f"Sygnał {signal['currency']} zamknięty - brak zdefiniowanych targetów")
        return True

    if current_level >= len(targets):
        signal["status"] = "CLOSED"
        signal["status_description"] = "All targets reached"
        log_to_file(f"Sygnał {signal['currency']} zamknięty - wszystkie targety osiągnięte")
        return True

    next_target = targets[current_level]
    target_reached = (current_price >= next_target if is_long else current_price <= next_target)

    if target_reached:
        try:
            symbol = signal["currency"]
            open_orders = client.get_open_orders(symbol=symbol)

            for order in open_orders:
                if order['type'] in ['STOP_LOSS_LIMIT', 'LIMIT_MAKER']:
                    try:
                        client.cancel_order(symbol=symbol, orderId=order['orderId'])
                        log_to_file(f"Anulowano zlecenie {order['orderId']} po osiągnięciu targetu {current_level + 1}")
                    except BinanceAPIException as cancel_error:
                        if 'Unknown order sent' in str(cancel_error):
                            log_to_file(f"Zlecenie {order['orderId']} już nie istnieje, pomijam")
                            continue
                        else:
                            log_to_file(f"Błąd anulowania zlecenia: {cancel_error}")
                            raise

            signal['current_target_level'] = current_level + 1
            entry_price = float(signal['real_entry'])
            new_stop_loss, take_profit = calculate_oco_levels(signal, entry_price)

            if new_stop_loss is None or take_profit is None:
                signal["status"] = "CLOSED"
                signal["status_description"] = "All targets reached"
                log_to_file(f"Sygnał {symbol} zamknięty - wszystkie targety osiągnięte")
                return True

            new_stop_loss = adjust_price(symbol, new_stop_loss)
            take_profit = adjust_price(symbol, take_profit)
            adjusted_quantity = adjust_quantity(symbol, base_balance)

            min_notional = get_min_notional(symbol)
            if adjusted_quantity * take_profit < min_notional or adjusted_quantity * new_stop_loss < min_notional:
                log_to_file(f"Nie można stworzyć OCO dla {symbol} - ilość {adjusted_quantity} za mała (TP: {adjusted_quantity * take_profit}, SL: {adjusted_quantity * new_stop_loss} < {min_notional})")
                signal["status"] = "CLOSED"
                signal["error"] = "INSUFFICIENT_AMOUNT_FOR_NEXT_TARGET"
                return True

            oco_order = create_oco_order_direct(
                client=client,
                symbol=symbol,
                side='SELL' if is_long else 'BUY',
                quantity=adjusted_quantity,
                take_profit_price=take_profit,
                stop_price=new_stop_loss,
                stop_limit_price=new_stop_loss
            )

            if oco_order:
                signal['oco_order_id'] = oco_order['orderListId']
                add_order_to_history(signal, oco_order, "OCO")
                log_to_file(f"Zaktualizowano OCO dla {symbol} po osiągnięciu targetu {current_level + 1}: SL={new_stop_loss}, TP={take_profit}, orderListId={oco_order['orderListId']}")
            else:
                log_to_file(f"Nie udało się utworzyć OCO dla {symbol} po osiągnięciu targetu {current_level + 1}")
                signal['current_target_level'] = current_level  # Cofnij poziom, jeśli OCO się nie udało
                signal["error"] = "FAILED_TO_CREATE_OCO"

        except Exception as e:
            log_to_file(f"Błąd aktualizacji OCO dla {symbol}: {e}")
            log_to_file(traceback.format_exc())
            signal['current_target_level'] = current_level  # Cofnij poziom w przypadku wyjątku
            signal["error"] = f"OCO_UPDATE_ERROR: {str(e)}"

    return signal["status"] == "CLOSED"

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