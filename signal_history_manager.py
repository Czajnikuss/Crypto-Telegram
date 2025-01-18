import json
import os
import time
from common import client, log_to_file, adjust_price, adjust_quantity, get_order_details

SIGNAL_HISTORY_FILE = 'signal_history.json'

def load_signal_history():
    if os.path.exists(SIGNAL_HISTORY_FILE):
        with open(SIGNAL_HISTORY_FILE, 'r') as file:
            return json.load(file)
    return []

def save_signal_history(history):
    with open(SIGNAL_HISTORY_FILE, 'w') as file:
        json.dump(history, file, indent=4)

def verify_position_status(signal, current_balance):
    """Sprawdza stan pozycji na podstawie salda"""
    market_order = next((o for o in signal["orders"] if o['type'] == 'MARKET' and o['status'] == 'FILLED'), None)
    if not market_order:
        return "INVALID"
        
    expected_quantity = float(market_order['executedQty'])
    tolerance = 0.001
    
    if abs(current_balance - expected_quantity) <= expected_quantity * tolerance:
        return "OPEN"
    elif current_balance < expected_quantity * (1 - tolerance):
        return "CLOSED"
    return "PARTIAL"

def check_position_closure(signal, orders):
    """Sprawdza czy pozycja powinna zostać zamknięta"""
    # Sprawdź czy którekolwiek zlecenie stop-loss zostało wykonane
    for order in orders:
        if (order['type'] == 'STOP_LOSS_LIMIT' and 
            order['status'] == 'FILLED'):
            return True
            
    # Sprawdź czy osiągnięto ostatni target
    highest_price = signal.get('highest_price', 0)
    is_long = signal['signal_type'] == 'LONG'
    last_target = signal['targets'][-1]
    
    if (is_long and highest_price >= last_target) or \
       (not is_long and highest_price <= last_target):
        return True
        
    return False


def calculate_dynamic_stop_loss(signal, highest_price, entry_price):
    """Oblicza poziom stop-loss na podstawie najwyższej osiągniętej ceny"""
    targets = signal["targets"]
    is_long = signal['signal_type'] == 'LONG'
    
    # Sprawdzamy który target został osiągnięty (używając highest_price)
    target_reached = None
    for i, target in enumerate(targets):
        if (highest_price >= target if is_long else highest_price <= target):
            target_reached = i
    
    if target_reached is None:
        return signal.get('stop_loss')
    
    # Po pierwszym targecie, stop-loss na entry
    if target_reached == 0:
        return entry_price
    
    # Po drugim targecie i kolejnych, stop-loss na poprzedni target
    return targets[target_reached - 1]


def update_signal_orders(signal):
    """Aktualizuje stan zleceń w sygnale"""
    symbol = signal["currency"]
    try:
        orders = [get_order_details(symbol, order['orderId']) for order in signal.get('orders', [])]
        signal['orders'] = [
            {
                "orderId": order['orderId'],
                "type": order['type'],
                "status": order['status'],
                "stopPrice": float(order.get('stopPrice', 0)),
                "side": order['side'],
                "quantity": float(order['origQty']),
                "executedQty": float(order['executedQty']),
                "price": float(order.get('price', 0)),
                "time": order['time']
            }
            for order in orders if order is not None
        ]
        return signal
    except Exception as e:
        log_to_file(f"Błąd podczas aktualizacji zleceń dla {symbol}: {e}")
        return signal

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


def check_and_update_signal_history():
    history = load_signal_history()
    
    for signal in history:
        if signal.get("status") != "CLOSED":
            try:
                symbol = signal["currency"]
                
                # Pobierz wszystkie aktywne zlecenia dla symbolu
                open_orders = client.get_open_orders(symbol=symbol)
                active_stop_loss = any(
                    order['type'] == 'STOP_LOSS_LIMIT' 
                    for order in open_orders
                )
                
                # Pobierz aktualną cenę i saldo
                ticker = client.get_symbol_ticker(symbol=symbol)
                current_price = float(ticker['price'])
                signal = update_signal_high_price(signal, current_price)
                
                symbol_info = client.get_symbol_info(symbol)
                base_asset = symbol_info['baseAsset']
                account = client.get_account()
                base_balance = float(next(
                    (b['free'] for b in account['balances'] if b['asset'] == base_asset), 
                    0
                ))
                
                log_to_file(f"Saldo {base_asset}: {base_balance}, Aktualna cena {symbol}: {current_price}")
                
                # Sprawdzamy ilość kupioną według historii zleceń
                executed_qty = 0
                for order in signal.get('orders', []):
                    if order['type'] == 'MARKET' and order['side'] == 'BUY':
                        executed_qty = float(order['executedQty'])
                        break
                
                if executed_qty > 0 and base_balance >= executed_qty:
                    # Mamy wystarczające saldo, pozycja jest otwarta
                    log_to_file(f"Pozycja aktywna dla {symbol}, saldo {base_balance} >= wymagane {executed_qty}")
                    continue
                else:
                    log_to_file(f"Brak wystarczającego salda dla {symbol}: {base_balance} < {executed_qty}")
                    signal["status"] = "CLOSED"
                    continue

                # Sprawdzenie statusu pozycji
                position_status = verify_position_status(signal, base_balance)
                if position_status in ["CLOSED", "INVALID"]:
                    signal["status"] = "CLOSED"
                    log_to_file(f"Pozycja zamknięta dla {symbol}")
                    continue

                entry_price = float(market_order['price'])
                is_long = signal['signal_type'] == 'LONG'

                # Sprawdzenie czy mamy aktywny stop-loss gdy posiadamy saldo
                if base_balance > 0 and not active_stop_loss:
                    # Oblicz właściwy poziom stop-loss
                    
                    try:
                        new_stop = calculate_dynamic_stop_loss(signal, signal['highest_price'], entry_price)
                        
                        adjusted_quantity = adjust_quantity(symbol, base_balance)
                        log_to_file(f"""
                        Próba utworzenia zlecenia stop-loss:
                        Symbol: {symbol}
                        Typ: STOP_LOSS_LIMIT
                        Strona: {'SELL' if is_long else 'BUY'}
                        Ilość: {adjusted_quantity}
                        Stop Price: {new_stop}
                        Aktualna cena: {current_price}
                        """)
                        
                        
                        new_stop_loss_order = client.create_order(
                            symbol=symbol,
                            side='SELL' if is_long else 'BUY',
                            type="STOP_LOSS_LIMIT",
                            timeInForce="GTC",
                            quantity=adjusted_quantity,
                            stopPrice=adjust_price(symbol, new_stop),
                            price=adjust_price(symbol, new_stop)
                        )
                        log_to_file(f"Utworzono brakujący stop-loss dla {symbol} na poziomie {new_stop}")
                        
                        # Dodaj nowe zlecenie do historii
                        signal["orders"].append({
                            "orderId": new_stop_loss_order['orderId'],
                            "type": "STOP_LOSS_LIMIT",
                            "status": "NEW",
                            "stopPrice": float(new_stop),
                            "side": 'SELL' if is_long else 'BUY',
                            "quantity": adjusted_quantity,
                            "executedQty": 0.0,
                            "price": float(new_stop),
                            "time": new_stop_loss_order['time']
                        })
                    except Exception as e:
                        log_to_file(f"Błąd podczas tworzenia brakującego stop-loss: {e}")
                
                if check_position_closure(signal, signal.get('orders', [])):
                    signal["status"] = "CLOSED"
                    log_to_file(f"Pozycja zamknięta dla {symbol} (stop-loss lub ostatni target)")
                    continue

                # Sprawdź osiągnięcie targetów
                for i, target in enumerate(signal["targets"]):
                    target_reached = (current_price >= target if is_long else current_price <= target)
                    if target_reached:
                        log_to_file(f"Osiągnięto cel {i+1} dla {symbol} przy cenie {current_price}")
                        
                        if i == len(signal["targets"]) - 1:  # Ostatni target
                            if base_balance > 0:
                                closing_side = 'SELL' if is_long else 'BUY'
                                adjusted_quantity = adjust_quantity(symbol, base_balance)
                                client.create_order(
                                    symbol=symbol,
                                    side=closing_side,
                                    type='MARKET',
                                    quantity=adjusted_quantity
                                )
                                signal["status"] = "CLOSED"
                                log_to_file(f"Zamknięto pozycję po osiągnięciu ostatniego celu dla {symbol}")
                                continue

                # Aktualizacja stop-loss jeśli jest aktywny
                if active_stop_loss:
                    new_stop = calculate_dynamic_stop_loss(signal, signal['highest_price'], entry_price)
                    current_stop = next(
                        (o['stopPrice'] for o in open_orders if o['type'] == 'STOP_LOSS_LIMIT'),
                        None
                    )
                    
                    if current_stop and abs(float(current_stop) - new_stop) > 0.0001:
                        for order in open_orders:
                            if order['type'] == 'STOP_LOSS_LIMIT':
                                try:
                                    client.cancel_order(symbol=symbol, orderId=order['orderId'])
                                    time.sleep(1)
                                    new_order = client.create_order(
                                        symbol=symbol,
                                        side='SELL' if is_long else 'BUY',
                                        type="STOP_LOSS_LIMIT",
                                        timeInForce="GTC",
                                        quantity=float(order['origQty']),
                                        stopPrice=adjust_price(symbol, new_stop),
                                        price=adjust_price(symbol, new_stop)
                                    )
                                    log_to_file(f"Zaktualizowano stop-loss dla {symbol} na poziom {new_stop}")
                                    
                                    # Aktualizuj listę zleceń w sygnale
                                    signal["orders"].append({
                                        "orderId": new_order['orderId'],
                                        "type": "STOP_LOSS_LIMIT",
                                        "status": "NEW",
                                        "stopPrice": float(new_stop),
                                        "side": 'SELL' if is_long else 'BUY',
                                        "quantity": float(order['origQty']),
                                        "executedQty": 0.0,
                                        "price": float(new_stop),
                                        "time": new_order['time']
                                    })
                                except Exception as e:
                                    log_to_file(f"Błąd aktualizacji stop-loss: {e}")

            except Exception as e:
                log_to_file(f"Błąd podczas sprawdzania historii dla {signal['currency']}: {e}")
                handle_critical_error(signal)

    save_signal_history(history)
