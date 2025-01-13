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

def calculate_dynamic_stop_loss(signal, current_price, entry_price):
    """Oblicza poziom stop-loss na podstawie aktualnej ceny i celów"""
    targets = signal["targets"]
    is_long = signal['signal_type'] == 'LONG'
    
    # Sprawdzamy który target został osiągnięty
    target_reached = None
    for i, target in enumerate(targets):
        if (current_price >= target if is_long else current_price <= target):
            target_reached = i
    
    if target_reached is None:
        return signal.get('stop_loss')  # Używamy początkowego stop-loss
    
    # Po pierwszym targecie, stop-loss powinien być na entry
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

def check_and_update_signal_history():
    history = load_signal_history()
    for signal in history:
        if signal.get("status") != "CLOSED":
            try:
                signal = update_signal_orders(signal)
                symbol = signal["currency"]
                
                # Pobierz aktualną cenę
                ticker = client.get_symbol_ticker(symbol=symbol)
                current_price = float(ticker['price'])
                
                # Sprawdź saldo
                account = client.get_account()
                symbol_info = client.get_symbol_info(symbol)
                base_asset = symbol_info['baseAsset']
                base_balance = float(next((b['free'] for b in account['balances'] if b['asset'] == base_asset), 0))
                log_to_file(f"Saldo {base_asset}: {base_balance}, Aktualna cena {symbol}: {current_price}")

                market_order = next((o for o in signal.get('orders', []) if o['type'] == 'MARKET' and o['status'] == 'FILLED'), None)
                if not market_order:
                    log_to_file(f"Brak zlecenia market dla {symbol}")
                    signal["status"] = "CLOSED"
                    continue

                position_status = verify_position_status(signal, base_balance)
                if position_status in ["CLOSED", "INVALID"]:
                    signal["status"] = "CLOSED"
                    log_to_file(f"Pozycja zamknięta dla {symbol}")
                    continue

                entry_price = float(market_order['price'])
                is_long = signal['signal_type'] == 'LONG'

                # Sprawdź czy osiągnęliśmy któryś z targetów
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

                # Aktualizuj stop-loss
                new_stop = calculate_dynamic_stop_loss(signal, current_price, entry_price)
                if new_stop:
                    stop_loss_orders = [o for o in signal["orders"] if o['type'] == 'STOP_LOSS_LIMIT']
                    for order in stop_loss_orders:
                        if order['status'] == 'NEW' and abs(float(order['stopPrice']) - new_stop) > 0.0001:
                            try:
                                client.cancel_order(symbol=symbol, orderId=order['orderId'])
                                time.sleep(1)
                                # W miejscu gdzie tworzymy nowe zlecenie stop-loss
                                new_order = client.create_order(
                                    symbol=symbol,
                                    side='SELL' if is_long else 'BUY',
                                    type="STOP_LOSS_LIMIT",
                                    timeInForce="GTC",
                                    quantity=float(order['quantity']),
                                    stopPrice=new_stop,
                                    price=new_stop
                                )
                                # Dodaj nowe zlecenie do listy zleceń
                                signal["orders"].append({
                                    "orderId": new_order['orderId'],
                                    "type": "STOP_LOSS_LIMIT",
                                    "status": "NEW",
                                    "stopPrice": float(new_stop),
                                    "side": 'SELL' if is_long else 'BUY',
                                    "quantity": float(order['quantity']),
                                    "executedQty": 0.0,
                                    "price": float(new_stop),
                                    "time": new_order['time']
                                })

                                log_to_file(f"Zaktualizowano stop-loss dla {symbol} na poziom {new_stop}")
                            except Exception as e:
                                log_to_file(f"Błąd aktualizacji stop-loss: {e}")

            except Exception as e:
                log_to_file(f"Błąd podczas sprawdzania historii dla {signal['currency']}: {e}")

    save_signal_history(history)
