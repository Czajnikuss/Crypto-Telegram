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
    """Aktualizuje najwyższą osiągniętą cenę w sygnale oraz przechowuje historię ostatnich 5 cen"""
    is_long = signal['signal_type'] == 'LONG'
    current_high = signal.get('highest_price', current_price if is_long else float('inf'))

    if is_long:
        signal['highest_price'] = max(current_high, current_price)
    else:
        signal['highest_price'] = min(current_high, current_price)

    # Dodaj aktualną cenę do historii cen
    price_history = signal.get('price_history', [])
    price_history.append(current_price)
    
    # Zachowaj maksymalnie 5 ostatnich cen
    if len(price_history) > 5:
        price_history = price_history[-5:]
    
    signal['price_history'] = price_history
    
    # Sprawdź, czy osiągnięto cel 1 i czy cena spadła o 5 ticków poniżej maksymalnej
    if signal.get('current_target_level', 0) >= 1:
        symbol_info = client.get_symbol_info(signal['currency'])
        tick_size = float(next(filter(lambda f: f['filterType'] == 'PRICE_FILTER', symbol_info['filters']))['tickSize'])
        
        price_difference = abs(signal['highest_price'] - current_price)
        ticks_difference = price_difference / tick_size
        
        if ticks_difference >= 5:
            signal['status'] = "CLOSED"
            signal['status_description'] = f"Closed after target 1 - price dropped by 5 ticks from highest"
            signal['exit_price'] = current_price
            signal['exit_time'] = int(time.time() * 1000)
            log_to_file(f"Zamknięto pozycję dla {signal['currency']} po spadku o 5 ticków od maksimum po celu 1")
            close_remaining_balance(signal)
    
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

def get_total_balance():
    """Pobiera pełne saldo (wolne + zablokowane) dla wszystkich aktywów."""
    try:
        account = client.get_account()
        balances = {}
        for asset_info in account['balances']:
            asset = asset_info['asset']
            total = float(asset_info['free']) + float(asset_info['locked'])
            if total > 0:
                balances[asset] = total
        return balances
    except Exception as e:
        log_to_file(f"Błąd podczas pobierania całkowitych sald: {e}")
        return {}
    
    

def check_and_update_signal_history():
    history = load_signal_history()
    updated = False
    all_balances = get_total_balance()

    for signal in history:
        if signal.get("status") != "OPEN":
            continue

        symbol = signal["currency"]
        base_asset = symbol.replace("USDT", "")
        active_oco = None

        try:
            # Pobranie aktualnej ceny
            current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
            signal = update_signal_high_price(signal, current_price)
            updated = True

            # Pobranie salda
            base_balance = all_balances.get(base_asset, 0)

            # Pobranie informacji o symbolu, jeśli brak
            if "symbol_info" not in signal:
                signal["symbol_info"] = client.get_symbol_info(symbol)
                log_to_file(f"Pobrano symbol_info dla {symbol}")
            symbol_info = signal["symbol_info"]

            # Minimalna wartość notionalna
            notional_filter = next(
                (f for f in symbol_info['filters'] if f['filterType'] == 'NOTIONAL'),
                None
            )
            min_notional = float(notional_filter['minNotional']) if notional_filter else 0
            notional_value = base_balance * current_price

            # Sprawdzenie aktywnych zleceń OCO
            all_oco_orders = client.get_open_orders(symbol=symbol)
            oco_order_id = signal.get("oco_order_id")
            active_oco = any(order.get('orderListId') == oco_order_id for order in all_oco_orders)

            # Określenie celów i osiągniętego celu
            targets = signal.get("targets", [])
            achieved_target = None
            for i, target in enumerate(targets):
                if (signal["signal_type"] == "LONG" and current_price >= float(target)) or \
                   (signal["signal_type"] == "SHORT" and current_price <= float(target)):
                    achieved_target = i + 1
                else:
                    break

            # Logowanie
            log_to_file(
                f"Przetwarzanie {symbol}: "
                f"cena={current_price:.4f}, "
                f"saldo={base_balance:.2f}, "
                f"notional={notional_value:.2f}, "
                f"min_notional={min_notional:.2f}, "
                f"active_oco={active_oco}, "
                f"cele={targets}, "
                f"osiągnięty_cel={achieved_target if achieved_target else 'Brak'}"
            )

            # Sprawdzenie historii OCO, jeśli nie jest aktywne, ale było zdefiniowane
            if oco_order_id and not active_oco:
                # Pobierz historię zleceń
                trades = client.get_my_trades(symbol=symbol)
                oco_orders = signal.get("orders", [])
                
                # Znajdź zlecenia OCO
                stop_loss_order = next((o for o in oco_orders if o["type"] == "STOP_LOSS_LIMIT" and o["oco_group_id"] == oco_order_id), None)
                take_profit_order = next((o for o in oco_orders if o["type"] == "LIMIT_MAKER" and o["oco_group_id"] == oco_order_id), None)
                
                # Sprawdź historię handlu, aby znaleźć zrealizowane zlecenie
                filled_order = None
                for trade in trades:
                    # Sprawdź czy zlecenie Stop Loss zostało zrealizowane
                    if stop_loss_order and trade.get("orderId") == stop_loss_order.get("orderId") and float(trade.get("qty", 0)) > 0:
                        log_to_file(f"OCO dla {symbol} zrealizowane na Stop Loss przy cenie {trade['price']}")
                        signal["status"] = "CLOSED"
                        signal["status_description"] = f"Stop Loss wykonany przy cenie {trade['price']}"
                        filled_order = {
                            'price': trade['price'],
                            'executedQty': trade['qty'],
                            'type': 'STOP_LOSS_LIMIT',
                            'time': trade['time']
                        }
                        signal["exit_time"] = trade["time"]
                        updated = True
                        break
                    # Sprawdź czy zlecenie Take Profit zostało zrealizowane
                    elif take_profit_order and trade.get("orderId") == take_profit_order.get("orderId") and float(trade.get("qty", 0)) > 0:
                        log_to_file(f"OCO dla {symbol} zrealizowane na Take Profit przy cenie {trade['price']}")
                        signal["status"] = "CLOSED"
                        signal["status_description"] = f"Take Profit wykonany przy cenie {trade['price']}"
                        filled_order = {
                            'price': trade['price'],
                            'executedQty': trade['qty'],
                            'type': 'LIMIT_MAKER',
                            'time': trade['time']
                        }
                        signal["exit_time"] = trade["time"]
                        updated = True
                        break
                
                # Jeśli znaleziono zrealizowane zlecenie, aktualizuj informacje o zysku
                if filled_order:
                    update_signal_with_profit_info(signal, filled_order)
                # Jeśli nie znaleziono realizacji w historii, ale saldo jest znacznie mniejsze niż poprzednio, 
                # to zlecenie mogło zostać zrealizowane, ale nie znaleźliśmy go w historii
                elif base_balance < float(signal.get('real_amount', 0)) * 0.5:
                    log_to_file(f"OCO dla {symbol} prawdopodobnie zrealizowane, ale nie znaleziono w historii. Saldo: {base_balance}")
                    signal["status"] = "CLOSED"
                    signal["status_description"] = "OCO prawdopodobnie zrealizowane"
                    signal["exit_price"] = current_price
                    signal["exit_time"] = int(time.time() * 1000)
                    close_remaining_balance(signal)
                    updated = True
                # Jeśli brak realizacji w historii, OCO mogło wygasnąć
                elif base_balance > 0:
                    log_to_file(f"OCO dla {symbol} (ID: {oco_order_id}) wygasło, saldo nadal istnieje: {base_balance}")
                    # Zamykamy pozycję ręcznie
                    signal["status"] = "CLOSED"
                    signal["status_description"] = "OCO wygasło, zamknięcie ręczne"
                    signal["exit_price"] = current_price
                    signal["exit_time"] = int(time.time() * 1000)
                    close_remaining_balance(signal)
                    updated = True

            # Zamknięcie, jeśli wszystkie cele osiągnięte i brak OCO
            elif active_oco is False and achieved_target and achieved_target >= len(targets):
                log_to_file(f"Zamykanie sygnału {symbol}: Wszystkie cele osiągnięte (ostatni cel: {targets[-1]}) bez aktywnego OCO")
                signal["status"] = "CLOSED"
                signal["status_description"] = f"Wszystkie cele osiągnięte przy cenie {current_price:.4f}"
                signal["exit_price"] = current_price
                signal["exit_time"] = int(time.time() * 1000)
                close_remaining_balance(signal)
                updated = True

            # Zamknięcie, jeśli brak salda i brak OCO
            elif not active_oco and base_balance == 0:
                log_to_file(f"Zamykanie sygnału {symbol}: Brak salda i aktywnego OCO")
                signal["status"] = "CLOSED"
                signal["status_description"] = "Brak salda i aktywnego OCO"
                signal["exit_time"] = int(time.time() * 1000)
                updated = True

            # Aktualizacja OCO przy osiągnięciu targetu
            elif active_oco and achieved_target and achieved_target > signal.get('current_target_level', 0):
                updated = handle_targets(signal, current_price, base_balance) or updated

        except Exception as e:
            log_to_file(f"Błąd podczas przetwarzania {symbol}: {str(e)}")
            if active_oco:
                log_to_file(f"Pominięto zamknięcie sygnału dla {symbol} - OCO jest aktywne")
            else:
                log_to_file(f"Zamknięto sygnał dla {symbol} z powodu błędu: {str(e)}")
                signal["status"] = "CLOSED"
                updated = True

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
    """Oblicza poziomy dla OCO na podstawie aktualnego poziomu targetu i nowych zasad"""
    targets = validate_targets(signal)
    current_level = signal.get('current_target_level', 0)
    is_long = signal['signal_type'] == 'LONG'
    
    # Log debugging info
    log_to_file(f"Przeliczanie poziomów OCO dla {signal['currency']}: poziom={current_level}, entry={entry_price}, targets={targets}")

    if len(targets) < 2:  # Potrzebujemy co najmniej 2 targetów
        log_to_file(f"Sygnał {signal['currency']} nie ma wystarczających targetów - zwracam None")
        return None, None

    # Take Profit zawsze na poziomie targetu 2 (indeks 1)
    take_profit = float(targets[1])

    # Stop Loss według zasad
    if current_level == 0:
        # Poziom 0: użyj stop_loss z sygnału lub domyślny
        stop_loss_from_signal = signal.get("stop_loss")
        if stop_loss_from_signal is not None:
            stop_loss = float(stop_loss_from_signal)
            # Verify stop loss makes sense
            if (is_long and stop_loss >= entry_price) or (not is_long and stop_loss <= entry_price):
                stop_loss = entry_price * (0.95 if is_long else 1.05)
                log_to_file(f"Sygnał {signal['currency']} - stop_loss z sygnału ({stop_loss_from_signal}) niewłaściwy, ustawiono {stop_loss}")
        else:
            stop_loss = entry_price * (0.95 if is_long else 1.05)
            log_to_file(f"Sygnał {signal['currency']} - brak stop_loss w sygnale, ustawiono {stop_loss}")
    elif current_level == 1:
        # Poziom 1: Stop Loss na poziomie połowy między real_entry a target 1
        target_1 = float(targets[0])
        if is_long:
            stop_loss = entry_price + (target_1 - entry_price) / 2
        else:
            stop_loss = entry_price - (entry_price - target_1) / 2
        log_to_file(f"Sygnał {signal['currency']} - ustawiono stop_loss na połowie między real_entry a target 1: {stop_loss}")
    else:
        # Poziom 2+: Stop Loss na poziomie targetu 0 (pierwszy target)
        stop_loss = float(targets[0])
        log_to_file(f"Sygnał {signal['currency']} - ustawiono stop_loss na poziomie targetu 0: {stop_loss}")

    # Validate price relationships for OCO orders
    is_valid = True
    if is_long:
        if stop_loss >= take_profit:
            log_to_file(f"Sygnał {signal['currency']} - stop loss ({stop_loss}) >= take profit ({take_profit})")
            is_valid = False
        if stop_loss >= entry_price and current_level == 0:
            log_to_file(f"Sygnał {signal['currency']} - stop loss ({stop_loss}) >= entry ({entry_price}) na poziomie 0")
            is_valid = False
        if take_profit <= entry_price:
            log_to_file(f"Sygnał {signal['currency']} - take profit ({take_profit}) <= entry ({entry_price})")
            is_valid = False
    else:
        if stop_loss <= take_profit:
            log_to_file(f"Sygnał {signal['currency']} - stop loss ({stop_loss}) <= take profit ({take_profit})")
            is_valid = False
        if stop_loss <= entry_price and current_level == 0:
            log_to_file(f"Sygnał {signal['currency']} - stop loss ({stop_loss}) <= entry ({entry_price}) na poziomie 0")
            is_valid = False
        if take_profit >= entry_price:
            log_to_file(f"Sygnał {signal['currency']} - take profit ({take_profit}) >= entry ({entry_price})")
            is_valid = False

    if not is_valid:
        log_to_file(f"Sygnał {signal['currency']} - nieprawidłowa relacja cen: SL={stop_loss}, TP={take_profit}, Entry={entry_price}")
        return None, None
        
    log_to_file(f"Poprawne poziomy OCO dla {signal['currency']}: SL={stop_loss}, TP={take_profit}")
    return stop_loss, take_profit

def close_remaining_balance(signal):
    """Zamyka pozostałe saldo dla danej waluty jako zlecenie market"""
    symbol = signal["currency"]
    try:
        base_balance = get_base_balance(symbol)
        if base_balance > 0:
            adjusted_quantity = adjust_quantity(symbol, base_balance)
            if adjusted_quantity > 0:
                closing_side = 'SELL' if signal['signal_type'] == 'LONG' else 'BUY'
                client.create_order(
                    symbol=symbol,
                    side=closing_side,
                    type='MARKET',
                    quantity=adjusted_quantity
                )
                log_to_file(f"Zamknięto pozostałe saldo dla {symbol}, ilość: {adjusted_quantity}")
            else:
                log_to_file(f"Pozostałe saldo dla {symbol} zbyt małe do zamknięcia: {base_balance}")
        else:
            log_to_file(f"Brak pozostałego salda do zamknięcia dla {symbol}")
    except Exception as e:
        log_to_file(f"Błąd podczas zamykania pozostałego salda dla {symbol}: {e}")


def handle_targets(signal, current_price, base_balance):
    from binance_trading import add_order_to_history
    """Aktualizuje OCO przy osiągnięciu kolejnego targetu i według nowych zasad"""
    is_long = signal['signal_type'] == 'LONG'
    targets = validate_targets(signal)
    symbol = signal["currency"]
    current_level = signal.get('current_target_level', 0)
    
    # Log debug info at start
    log_to_file(f"Sprawdzanie targetów dla {symbol}: current_price={current_price}, current_level={current_level}, targets={targets}")

    if len(targets) < 2:
        signal["status"] = "CLOSED"
        signal["error"] = "INSUFFICIENT_TARGETS"
        log_to_file(f"Sygnał {symbol} zamknięty - niewystarczająca liczba targetów")
        close_remaining_balance(signal)
        return True

    if current_level >= len(targets):
        signal["status"] = "CLOSED"
        signal["status_description"] = "All targets reached"
        log_to_file(f"Sygnał {symbol} zamknięty - wszystkie targety osiągnięte")
        close_remaining_balance(signal)
        return True

    # Check if current price has reached the next target
    next_target = float(targets[current_level])
    target_reached = (current_price >= next_target if is_long else current_price <= next_target)
    
    # Debug log
    log_to_file(f"Sprawdzanie targetu {current_level} dla {symbol}: cena={current_price}, target={next_target}, osiągnięty={target_reached}")

    # Sprawdź trend cen po osiągnięciu celu 1
    if current_level == 1:
        price_history = signal.get('price_history', [])
        if len(price_history) >= 3:  # Sprawdzamy trend tylko jeśli mamy co najmniej 3 ceny
            is_downtrend = all(price_history[i] > price_history[i+1] for i in range(len(price_history)-1))
            is_uptrend = all(price_history[i] < price_history[i+1] for i in range(len(price_history)-1))
            
            # Zamknij pozycję, jeśli trend jest przeciwny do sygnału
            if (is_long and is_downtrend) or (not is_long and is_uptrend):
                log_to_file(f"Wykryto przeciwny trend dla {symbol} po osiągnięciu celu 1 - zamykanie pozycji")
                closing_side = 'SELL' if is_long else 'BUY'
                adjusted_quantity = adjust_quantity(symbol, base_balance)
                if adjusted_quantity > 0:
                    try:
                        order = client.create_order(
                            symbol=symbol,
                            side=closing_side,
                            type='MARKET',
                            quantity=adjusted_quantity
                        )
                        log_to_file(f"Zamknięto pozycję dla {symbol} z powodu przeciwnego trendu, ilość: {adjusted_quantity}")
                        signal["status"] = "CLOSED"
                        signal["status_description"] = "Closed due to adverse price trend after target 1"
                        signal["exit_price"] = current_price
                        signal["exit_time"] = int(time.time() * 1000)
                        close_remaining_balance(signal)
                        return True
                    except Exception as e:
                        log_to_file(f"Błąd podczas zamykania pozycji z powodu trendu: {e}")

    # Skip targets that have been reached
    if target_reached:
        current_level += 1
        signal['current_target_level'] = current_level
        log_to_file(f"Target {current_level-1} osiągnięty dla {symbol}, przechodzenie na poziom {current_level}")

        try:
            # Save the updated level
            signal['current_target_level'] = current_level
            
            # Cancel existing orders
            open_orders = client.get_open_orders(symbol=symbol)
            for order in open_orders:
                if order['type'] in ['STOP_LOSS_LIMIT', 'LIMIT_MAKER']:
                    try:
                        client.cancel_order(symbol=symbol, orderId=order['orderId'])
                        log_to_file(f"Anulowano zlecenie {order['orderId']} po osiągnięciu targetu {current_level-1}")
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
                signal["status_description"] = "Invalid OCO levels"
                log_to_file(f"Sygnał {symbol} zamknięty - nieprawidłowe poziomy OCO")
                close_remaining_balance(signal)
                return True

            # Check for 50% difference between targets[0] and targets[1] only when reaching targets[0]
            if current_level == 1:
                target_1 = float(targets[0])
                target_2 = float(targets[1])
                mid_point = target_1 + (target_2 - target_1) * 0.5 if is_long else target_1 - (target_1 - target_2) * 0.5
                reached_mid_point = (current_price >= mid_point if is_long else current_price <= mid_point)
                
                if reached_mid_point:
                    new_stop_loss = target_1
                    log_to_file(f"Osiągnięto 50% między targetami dla {symbol} przy przechodzeniu na poziom 1, ustawiono stop_loss na target 1: {new_stop_loss}")

            # Adjust prices and quantity for exchange requirements
            new_stop_loss = adjust_price(symbol, new_stop_loss)
            take_profit = adjust_price(symbol, take_profit)
            adjusted_quantity = adjust_quantity(symbol, base_balance)

            # Verify minimum notional value
            min_notional = get_min_notional(symbol)
            notional_tp = adjusted_quantity * take_profit
            notional_sl = adjusted_quantity * new_stop_loss
            
            log_to_file(f"Notional values for {symbol}: TP={notional_tp}, SL={notional_sl}, min required={min_notional}")
            
            if notional_tp < min_notional or notional_sl < min_notional:
                log_to_file(f"Nie można stworzyć OCO dla {symbol} - wartość za mała: TP={notional_tp}, SL={notional_sl} < {min_notional}")
                signal["status"] = "CLOSED"
                signal["error"] = f"INSUFFICIENT_AMOUNT_FOR_NEXT_TARGET: TP={notional_tp}, SL={notional_sl} < {min_notional}"
                close_remaining_balance(signal)
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
                log_to_file(f"Zaktualizowano OCO dla {symbol} po osiągnięciu targetu: SL={new_stop_loss}, TP={take_profit}, orderListId={oco_order['orderListId']}")
            else:
                # If we failed to create OCO when reaching mid-point at level 1, close the position immediately
                if current_level == 1 and reached_mid_point:
                    log_to_file(f"Nie udało się utworzyć OCO dla {symbol} na poziomie 1 z mid-point - natychmiastowe zamknięcie pozycji")
                    closing_side = 'SELL' if is_long else 'BUY'
                    adjusted_quantity = adjust_quantity(symbol, base_balance)
                    if adjusted_quantity > 0:
                        client.create_order(
                            symbol=symbol,
                            side=closing_side,
                            type='MARKET',
                            quantity=adjusted_quantity
                        )
                        log_to_file(f"Natychmiastowe zamknięcie pozycji dla {symbol}, ilość: {adjusted_quantity}")
                    signal["status"] = "CLOSED"
                    signal["status_description"] = "Failed to create OCO at mid-point"
                    close_remaining_balance(signal)
                    return True
                else:
                    log_to_file(f"Nie udało się utworzyć OCO dla {symbol} - pozostawiam sygnał otwarty")
                    signal["error"] = "FAILED_TO_CREATE_OCO"

        except Exception as e:
            log_to_file(f"Błąd aktualizacji OCO dla {symbol}: {e}")
            log_to_file(traceback.format_exc())
            signal["error"] = f"OCO_UPDATE_ERROR: {str(e)}"

    # Check if closed
    if signal.get("status") == "CLOSED":
        close_remaining_balance(signal)
        return True
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