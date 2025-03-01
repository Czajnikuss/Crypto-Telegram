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
        # Ensure we have valid data
        if not filled_order or 'price' not in filled_order or 'executedQty' not in filled_order:
            log_to_file(f"Nieprawidłowy format wypełnionego zlecenia: {filled_order}")
            return
            
        exit_price = float(filled_order['price'])
        exit_quantity = float(filled_order['executedQty'])

        # Calculate profit
        profit, amount_diff = calculate_profit(signal, exit_price, exit_quantity)

        if profit is not None:
            # Update signal with profit information
            signal['exit_price'] = exit_price
            signal['exit_quantity'] = exit_quantity
            signal['real_gain'] = profit
            signal['amount_difference'] = amount_diff
            
            # Determine exit type (take profit or stop loss)
            exit_type = "TAKE_PROFIT" if filled_order.get('type') == 'LIMIT_MAKER' else "STOP_LOSS"
            signal['exit_type'] = exit_type
            
            # Calculate profit percentage
            profit_percentage = (profit / (float(signal['real_entry']) * exit_quantity)) * 100
            signal['profit_percentage'] = profit_percentage
            
            # Update status description
            signal['status_description'] = f"{'Gain' if profit > 0 else 'Loss'}: {profit:.2f} - Amount: {exit_quantity:.4f} - Percentage: {profit_percentage:.2f}%"
            log_to_file(f"Sygnał zamknięty: {signal['status_description']}")
        else:
            log_to_file("Nie udało się obliczyć zysku/straty.")

    except Exception as e:
        log_to_file(f"Błąd podczas aktualizacji informacji o zysku w sygnale: {e}")

def check_and_update_signal_history():
    from binance_trading import add_order_to_history
    history = load_signal_history()
    updated = False

    for signal in history:
        if signal.get("status") == "CLOSED":
            continue

        try:
            symbol = signal["currency"]
            current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
            base_balance = get_base_balance(symbol)
            signal = update_signal_high_price(signal, current_price)
            updated = True

            # Initialize current target level if not present
            if 'current_target_level' not in signal:
                signal['current_target_level'] = 0

            # Get min notional for this symbol
            min_notional = get_min_notional(symbol)
            
            # Check if we have active OCO orders - with proper error handling
            try:
                all_oco_orders = get_all_oco_orders_for_symbol(client, symbol, only_active=True)
                active_oco = next((oco for oco in all_oco_orders 
                                if oco['orderListId'] == signal.get("oco_order_id")), None) if all_oco_orders else None
            except Exception as e:
                log_to_file(f"Błąd podczas pobierania OCO zleceń: {e}")
                active_oco = None  # Handle error case safely

            # Handle small remaining balance case
            notional_value = base_balance * current_price
            if base_balance > 0 and notional_value < min_notional * 1.1:
                log_to_file(f"Mała kwota na koncie dla {symbol}: {base_balance} (wartość: {notional_value} < {min_notional * 1.1})")
                
                # Check order status to determine if we should close position
                if "orders" in signal:
                    last_orders = signal["orders"]
                    # Check if all orders in our group are no longer active
                    all_filled_or_closed = all(
                        order["status"] in ["FILLED", "CANCELED", "EXPIRED"]
                        for order in last_orders if "oco_group_id" in order and order["oco_group_id"] == signal.get("oco_order_id")
                    )
                    
                    if all_filled_or_closed:
                        # Find filled order if any
                        filled_order = next((o for o in last_orders if o["status"] == "FILLED" and 
                                          o.get("oco_group_id") == signal.get("oco_order_id")), None)
                        
                        if filled_order:
                            # Update signal with profit info
                            update_signal_with_profit_info(signal, filled_order)
                            signal.update({
                                "status": "CLOSED",
                                "exit_time": filled_order["time"],
                            })
                            log_to_file(f"Sygnał {symbol} zamknięty na podstawie historii: {signal['status_description']}")
                        else:
                            # Close signal with residual balance info
                            signal.update({
                                "status": "CLOSED",
                                "error": "RESIDUAL_BALANCE_CLOSED",
                                "status_description": f"Zamknięto z pozostałością {base_balance} bez zysku"
                            })
                            log_to_file(f"Sygnał {symbol} zamknięty z pozostałością {base_balance} bez wykonanych zleceń")
                        save_signal_history(history)
                        continue

            # Handle case when OCO is missing but balance exists
            if not active_oco and base_balance > 0:
                # Check if balance is too small for new orders
                notional_value = base_balance * current_price
                if notional_value < min_notional:
                    log_to_file(f"Pominięto tworzenie OCO dla {symbol}: wartość {notional_value} poniżej MIN_NOTIONAL {min_notional}")
                    signal["status"] = "CLOSED"
                    signal["error"] = f"RESIDUAL_AMOUNT_TOO_SMALL: {base_balance} ({notional_value} < {min_notional})"
                    save_signal_history(history)
                    continue

                # Cancel any existing open orders
                open_orders = client.get_open_orders(symbol=symbol)
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

                # Calculate appropriate OCO levels
                entry_price = float(signal['real_entry'])
                stop_loss, take_profit = calculate_oco_levels(signal, entry_price)
                
                # Check if we need to close position (all targets reached)
                if stop_loss is None or take_profit is None:
                    # Check if we reached all targets
                    targets = validate_targets(signal)
                    current_level = signal.get('current_target_level', 0)
                    if current_level >= len(targets):
                        signal["status"] = "CLOSED"
                        signal["status_description"] = "All targets reached"
                        log_to_file(f"Sygnał {symbol} zamknięty - wszystkie targety osiągnięte")
                        save_signal_history(history)
                        continue
                    else:
                        # Some other error with levels calculation
                        signal["status"] = "CLOSED"
                        signal["error"] = "INVALID_OCO_LEVELS"
                        log_to_file(f"Sygnał {symbol} zamknięty - nie można obliczyć poziomów OCO")
                        save_signal_history(history)
                        continue

                # Adjust prices and quantity for exchange requirements
                stop_loss = adjust_price(symbol, stop_loss)
                take_profit = adjust_price(symbol, take_profit)
                adjusted_quantity = adjust_quantity(symbol, base_balance)

                # Check if adjusted order meets minimum notional requirements
                adjusted_notional_tp = adjusted_quantity * take_profit
                adjusted_notional_sl = adjusted_quantity * stop_loss
                
                if adjusted_notional_tp < min_notional or adjusted_notional_sl < min_notional:
                    log_to_file(f"Po dostosowaniu ilości: wartość OCO dla {symbol} (TP: {adjusted_notional_tp}, SL: {adjusted_notional_sl}) poniżej MIN_NOTIONAL {min_notional}")
                    signal["status"] = "CLOSED"
                    signal["error"] = f"ADJUSTED_AMOUNT_TOO_SMALL: {adjusted_quantity} (TP: {adjusted_notional_tp}, SL: {adjusted_notional_sl} < {min_notional})"
                    save_signal_history(history)
                    continue

                # Create new OCO order
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

            # Check active OCO orders for filled status
            if active_oco:
                try:
                    order_reports = get_order_reports(client, active_oco['orderListId'], symbol)
                    filled_order = next((r for r in order_reports if r['status'] == 'FILLED'), None)
                    is_any_expired_or_canceled = any(report['status'] in ['CANCELED', 'EXPIRED'] for report in order_reports)

                    if filled_order:
                        # OCO order filled, update profit info and close signal
                        update_signal_with_profit_info(signal, filled_order)
                        signal.update({
                            "status": "CLOSED",
                            "exit_time": filled_order['time'],
                        })
                        log_to_file(f"OCO zostało zrealizowane. Typ: {signal.get('exit_type', 'unknown')}, Cena: {signal.get('exit_price', 'unknown')}")
                    elif is_any_expired_or_canceled and base_balance < (min_notional / current_price) * 0.5:
                        # OCO partially filled or canceled, check for small remaining balance
                        filled_order = next((r for r in order_reports if r['status'] == 'FILLED'), None)
                        if filled_order:
                            update_signal_with_profit_info(signal, filled_order)
                            signal.update({
                                "status": "CLOSED",
                                "exit_time": filled_order['time'],
                            })
                            log_to_file(f"OCO zamknięte z jednym zleceniem wykonanym wcześniej. Typ: {signal.get('exit_type', 'unknown')}, Cena: {signal.get('exit_price', 'unknown')}")
                        else:
                            signal.update({
                                "status": "CLOSED",
                                "error": "OCO_EXPIRED_WITHOUT_FILL",
                                "status_description": "OCO wygasło bez pełnego wykonania"
                            })
                            log_to_file(f"OCO dla {symbol} wygasło bez realizacji pozycji")
                except Exception as e:
                    log_to_file(f"Błąd podczas sprawdzania statusu OCO dla {symbol}: {e}")

            # Check and update targets based on current price
            if handle_targets(signal, current_price, base_balance):
                log_to_file(f"Sygnał {symbol} zamknięty przez handle_targets")
                signal["status"] = "CLOSED"

        except Exception as e:
            log_to_file(f"Błąd podczas przetwarzania {symbol}: {e}")
            log_to_file(traceback.format_exc())
            try:
                handle_critical_error(signal)  
            except Exception as critical_error:
                log_to_file(f"Błąd krytyczny dla {symbol}: {critical_error}")

    if updated:
        save_signal_history(history)


def validate_targets(signal):
    """Waliduje i sortuje targety w sygnale, usuwając nie-liczbowe wartości."""
    targets = signal.get("targets", [])
    is_long = signal['signal_type'] == 'LONG'
    
    # Filtruj tylko wartości liczbowe
    valid_targets = [float(t) for t in targets if isinstance(t, (int, float)) and not isinstance(t, bool)]
    
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
    
    # Log debugging info
    log_to_file(f"Przeliczanie poziomów OCO dla {signal['currency']}: poziom={current_level}, entry={entry_price}, targets={targets}")

    if not targets:
        log_to_file(f"Sygnał {signal['currency']} nie ma ważnych targetów - zwracam None")
        return None, None

    # Check if all targets have been reached
    if current_level >= len(targets):
        log_to_file(f"Sygnał {signal['currency']} - wszystkie targety osiągnięte")
        return None, None

    # Calculate stop_loss
    if current_level == 0:
        # Poziom 0: użyj stop_loss z sygnału, jeśli jest sensowny
        if stop_loss_from_signal is not None:
            stop_loss = float(stop_loss_from_signal)  # Ensure float type
            # Verify stop loss makes sense
            if (is_long and stop_loss >= entry_price) or (not is_long and stop_loss <= entry_price):
                # Invalid stop loss relationship
                stop_loss = entry_price * (0.95 if is_long else 1.05)  # Less aggressive default
                log_to_file(f"Sygnał {signal['currency']} - stop_loss z sygnału ({stop_loss_from_signal}) niewłaściwy, ustawiono {stop_loss}")
        else:
            # No stop loss defined in signal
            stop_loss = entry_price * (0.95 if is_long else 1.05)
            log_to_file(f"Sygnał {signal['currency']} - brak stop_loss w sygnale, ustawiono {stop_loss}")
    else:
        # For level 1+: use entry price (level 1) or previous target as stop loss
        if current_level == 1:
            # Ensure stop loss is different from entry by a small amount to avoid API errors
            buffer = entry_price * 0.001  # 0.1% buffer
            stop_loss = entry_price - buffer if is_long else entry_price + buffer
            log_to_file(f"Sygnał {signal['currency']} - ustawiono stop_loss z buforem: {stop_loss}")
        else:
            # Use previous target as stop loss
            stop_loss = targets[current_level - 2]

    # Take-profit on next target or keep the last one
    if current_level < len(targets):
        take_profit = targets[current_level]
    else:
        # Should not reach here due to earlier check, but as a safeguard
        log_to_file(f"Sygnał {signal['currency']} - nieprawidłowy poziom targetu {current_level} vs {len(targets)}")
        return None, None

    # Validate price relationships for OCO orders
    is_valid = True
    if is_long:
        # For LONG positions: stop_loss < entry < take_profit
        if stop_loss >= take_profit:
            log_to_file(f"Sygnał {signal['currency']} - stop loss ({stop_loss}) >= take profit ({take_profit})")
            is_valid = False
        if stop_loss >= entry_price:
            log_to_file(f"Sygnał {signal['currency']} - stop loss ({stop_loss}) >= entry ({entry_price})")
            is_valid = False
        if take_profit <= entry_price:
            log_to_file(f"Sygnał {signal['currency']} - take profit ({take_profit}) <= entry ({entry_price})")
            is_valid = False
    else:
        # For SHORT positions: take_profit < entry < stop_loss
        if stop_loss <= take_profit:
            log_to_file(f"Sygnał {signal['currency']} - stop loss ({stop_loss}) <= take profit ({take_profit})")
            is_valid = False
        if stop_loss <= entry_price:
            log_to_file(f"Sygnał {signal['currency']} - stop loss ({stop_loss}) <= entry ({entry_price})")
            is_valid = False
        if take_profit >= entry_price:
            log_to_file(f"Sygnał {signal['currency']} - take profit ({take_profit}) >= entry ({entry_price})")
            is_valid = False

    if not is_valid:
        log_to_file(f"Sygnał {signal['currency']} - nieprawidłowa relacja cen: SL={stop_loss}, TP={take_profit}, Entry={entry_price}")
        return None, None
        
    log_to_file(f"Poprawne poziomy OCO dla {signal['currency']}: SL={stop_loss}, TP={take_profit}")
    return stop_loss, take_profit


def handle_targets(signal, current_price, base_balance):
    from binance_trading import add_order_to_history
    """Aktualizuje OCO przy osiągnięciu kolejnego targetu"""
    is_long = signal['signal_type'] == 'LONG'
    targets = validate_targets(signal)  # Use validated targets
    symbol = signal["currency"]
    current_level = signal.get('current_target_level', 0)
    
    # Log debug info at start
    log_to_file(f"Sprawdzanie targetów dla {symbol}: current_price={current_price}, current_level={current_level}, targets={targets}")

    if not targets:
        signal["status"] = "CLOSED"
        signal["error"] = "NO_TARGETS_DEFINED"
        log_to_file(f"Sygnał {symbol} zamknięty - brak zdefiniowanych targetów")
        return True

    if current_level >= len(targets):
        signal["status"] = "CLOSED"
        signal["status_description"] = "All targets reached"
        log_to_file(f"Sygnał {symbol} zamknięty - wszystkie targety osiągnięte")
        return True

    # Check if current price has reached the next target
    next_target = float(targets[current_level])
    target_reached = (current_price >= next_target if is_long else current_price <= next_target)
    
    # Debug log
    log_to_file(f"Sprawdzanie targetu {current_level} dla {symbol}: cena={current_price}, target={next_target}, osiągnięty={target_reached}")

    # Skip targets that have been skipped due to price movement
    while target_reached and current_level < len(targets) - 1:
        # Move to next level and check if that target has been reached too
        current_level += 1
        signal['current_target_level'] = current_level
        log_to_file(f"Target {current_level-1} osiągnięty dla {symbol}, sprawdzanie następnego...")
        
        if current_level >= len(targets):
            signal["status"] = "CLOSED"
            signal["status_description"] = "All targets reached"
            log_to_file(f"Sygnał {symbol} zamknięty - wszystkie targety osiągnięte")
            return True
            
        next_target = float(targets[current_level])
        target_reached = (current_price >= next_target if is_long else current_price <= next_target)

    # If any target was reached, update OCO orders
    if current_level > signal.get('current_target_level', 0):
        try:
            # Save the updated level
            signal['current_target_level'] = current_level
            
            # Cancel existing orders
            open_orders = client.get_open_orders(symbol=symbol)
            for order in open_orders:
                if order['type'] in ['STOP_LOSS_LIMIT', 'LIMIT_MAKER']:
                    try:
                        client.cancel_order(symbol=symbol, orderId=order['orderId'])
                        log_to_file(f"Anulowano zlecenie {order['orderId']} po osiągnięciu targetu {current_level}")
                    except BinanceAPIException as cancel_error:
                        if 'Unknown order sent' in str(cancel_error):
                            log_to_file(f"Zlecenie {order['orderId']} już nie istnieje, pomijam")
                            continue
                        else:
                            log_to_file(f"Błąd anulowania zlecenia: {cancel_error}")
                            raise

            # Calculate new OCO levels
            entry_price = float(signal['real_entry'])
            new_stop_loss, take_profit = calculate_oco_levels(signal, entry_price)

            if new_stop_loss is None or take_profit is None:
                signal["status"] = "CLOSED"
                signal["status_description"] = "All targets reached or invalid levels"
                log_to_file(f"Sygnał {symbol} zamknięty - wszystkie targety osiągnięte lub nieprawidłowe poziomy")
                return True

            # Adjust prices and quantity for exchange requirements
            new_stop_loss = adjust_price(symbol, new_stop_loss)
            take_profit = adjust_price(symbol, take_profit)
            adjusted_quantity = adjust_quantity(symbol, base_balance)

            # Verify minimum notional value
            min_notional = get_min_notional(symbol)
            notional_tp = adjusted_quantity * take_profit
            notional_sl = adjusted_quantity * new_stop_loss
            
            log_to_file(f"Notional values for {symbol}: TP={notional_tp}, SL={notional_sl}, min required={min_notional}")
            
            # Check if the order meets minimum requirements
            if notional_tp < min_notional or notional_sl < min_notional:
                log_to_file(f"Nie można stworzyć OCO dla {symbol} - wartość za mała: TP={notional_tp}, SL={notional_sl} < {min_notional}")
                signal["status"] = "CLOSED"
                signal["error"] = f"INSUFFICIENT_AMOUNT_FOR_NEXT_TARGET: TP={notional_tp}, SL={notional_sl} < {min_notional}"
                return True

            # Create new OCO order
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
                log_to_file(f"Zaktualizowano OCO dla {symbol} po osiągnięciu targetu {current_level}: SL={new_stop_loss}, TP={take_profit}, orderListId={oco_order['orderListId']}")
            else:
                log_to_file(f"Nie udało się utworzyć OCO dla {symbol} po osiągnięciu targetu {current_level}")
                # If we failed to create a new OCO but targets are reached, still consider them reached
                if current_level >= len(targets):
                    signal["status"] = "CLOSED"
                    signal["status_description"] = "All targets reached"
                    signal["error"] = "FAILED_TO_CREATE_FINAL_OCO"
                    return True

        except Exception as e:
            log_to_file(f"Błąd aktualizacji OCO dla {symbol}: {e}")
            log_to_file(traceback.format_exc())
            signal["error"] = f"OCO_UPDATE_ERROR: {str(e)}"

    # Check if closed
    return signal.get("status") == "CLOSED"

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