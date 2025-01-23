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


def get_base_balance(symbol):
    """Pobiera saldo dla danej pary tradingowej"""
    symbol_info = client.get_symbol_info(symbol)
    base_asset = symbol_info['baseAsset']
    account = client.get_account()
    return float(next(
        (b['free'] for b in account['balances'] if b['asset'] == base_asset),
        0
    ))

def verify_basic_position(signal, base_balance):
    """Sprawdza podstawowe warunki pozycji"""
    executed_qty = 0
    for order in signal.get('orders', []):
        if order['type'] == 'MARKET' and order['side'] == 'BUY':
            executed_qty = float(order['executedQty'])
            break
    
    if executed_qty > 0 and base_balance >= executed_qty:
        log_to_file(f"Pozycja aktywna dla {signal['currency']}, saldo {base_balance} >= wymagane {executed_qty}")
        return True
    return False

def handle_targets(signal, current_price, base_balance):
    """Zarządza targetami i zamyka pozycję jeśli potrzeba"""
    is_long = signal['signal_type'] == 'LONG'
    
    for i, target in enumerate(signal["targets"]):
        target_reached = (current_price >= target if is_long else current_price <= target)
        if target_reached:
            log_to_file(f"Osiągnięto cel {i+1} dla {signal['currency']} przy cenie {current_price}")
            
            if i == len(signal["targets"]) - 1 and base_balance > 0:  # Ostatni target
                closing_side = 'SELL' if is_long else 'BUY'
                adjusted_quantity = adjust_quantity(signal['currency'], base_balance)
                client.create_order(
                    symbol=signal['currency'],
                    side=closing_side,
                    type='MARKET',
                    quantity=adjusted_quantity
                )
                return True
    return False



def create_stop_loss_order(signal, symbol, quantity, stop_price):
    is_long = signal['signal_type'] == 'LONG'
    new_order = client.create_order(
        symbol=symbol,
        side='SELL' if is_long else 'BUY',
        type="STOP_LOSS_LIMIT",
        timeInForce="GTC",
        quantity=quantity,
        stopPrice=adjust_price(symbol, stop_price),
        price=adjust_price(symbol, stop_price)
    )
    
    return {
        "orderId": new_order['orderId'],
        "type": "STOP_LOSS_LIMIT",
        "status": "NEW",
        "stopPrice": float(stop_price),
        "side": 'SELL' if is_long else 'BUY',
        "quantity": quantity,
        "executedQty": 0.0,
        "price": float(stop_price),
        "time": new_order['time']
    }

def handle_stop_loss(signal, current_price, base_balance, open_orders):
    if base_balance <= 0:
        return
        
    symbol = signal['currency']
    entry_price = float(signal['real_entry'])
    new_stop = calculate_dynamic_stop_loss(signal, signal['highest_price'], entry_price)
    active_stop_loss = any(order['type'] == 'STOP_LOSS_LIMIT' for order in open_orders)

    if not active_stop_loss:
        try:
            adjusted_quantity = adjust_quantity(symbol, base_balance)
            new_order_info = create_stop_loss_order(signal, symbol, adjusted_quantity, new_stop)
            signal["orders"].append(new_order_info)
            log_to_file(f"Utworzono stop-loss dla {symbol} na poziomie {new_stop}")
        except Exception as e:
            log_to_file(f"Błąd podczas tworzenia stop-loss: {e}")
    else:
        current_stop = next((float(o['stopPrice']) for o in open_orders if o['type'] == 'STOP_LOSS_LIMIT'), None)
        if current_stop and abs(current_stop - new_stop) > 0.0001:
            for order in open_orders:
                if order['type'] == 'STOP_LOSS_LIMIT':
                    try:
                        client.cancel_order(symbol=symbol, orderId=order['orderId'])
                        time.sleep(1)
                        new_order_info = create_stop_loss_order(
                            signal, symbol, float(order['origQty']), new_stop
                        )
                        signal["orders"].append(new_order_info)
                        log_to_file(f"Zaktualizowano stop-loss dla {symbol} na poziom {new_stop}")
                    except Exception as e:
                        log_to_file(f"Błąd aktualizacji stop-loss: {e}")

def check_and_update_signal_history():
    history = load_signal_history()
    
    for signal in history:
        if signal.get("status") == "CLOSED":
            continue
            
        try:
            symbol = signal["currency"]
            current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
            base_balance = get_base_balance(symbol)
            executed_qty = signal["real_amount"]
            
            # Sprawdzenie aktywnych zleceń OCO
            open_orders = client.get_open_orders(symbol=symbol)
            oco_order = find_oco_order(open_orders, signal.get("oco_order_id"))
            
            if not oco_order and signal.get("oco_order_id"):
                # OCO zostało wykonane - sprawdź historię zleceń
                order_history = client.get_all_orders(symbol=symbol)
                executed_order = find_executed_oco_order(order_history, signal["oco_order_id"])
                
                if executed_order:
                    entry_price = float(signal["entry_price"])
                    executed_price = float(executed_order["price"])
                    profit_usdt = (executed_price - entry_price) * executed_qty
                    profit_percent = ((executed_price / entry_price) - 1) * 100
                    
                    # Określ czy to był stop loss czy take profit
                    order_type = "Stop Loss" if executed_price <= float(signal["stop_loss"]) else "Take Profit"
                    
                    log_message = (
                        f"Zlecenie OCO wykonane dla {symbol}\n"
                        f"Typ: {order_type}\n"
                        f"Cena wykonania: {executed_price}\n"
                        f"Zysk/Strata: {profit_usdt:.2f} USDT ({profit_percent:.2f}%)"
                    )
                    
                    log_to_file(log_message)
                    
                    # Aktualizacja sygnału
                    signal.update({
                        "status": "CLOSED",
                        "exit_price": executed_price,
                        "profit_usdt": profit_usdt,
                        "profit_percent": profit_percent,
                        "exit_type": order_type,
                        "exit_time": executed_order["time"]
                    })
                    
            signal = update_signal_high_price(signal, current_price)
            
        except Exception as e:
            log_to_file(f"Błąd podczas przetwarzania {symbol}: {e}")
            handle_critical_error(signal)
            
    save_signal_history(history)

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






