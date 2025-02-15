import json
import os
import time
from common import client, log_to_file, adjust_price, adjust_quantity, get_order_details, create_oco_order_direct


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
            
            # Aktualizacja najwyższej ceny
            signal = update_signal_high_price(signal, current_price)
            
            # Sprawdź aktywną grupę OCO
            active_oco = next((o for o in signal.get("orders", []) 
                             if o.get('oco_group_id') and o['status'] in ['NEW', 'PARTIALLY_FILLED']), None)
            
            # Obsługa błędów związanych z nieaktualnymi zleceniami
            try:
                # Jeśli brak OCO ale jest pozycja - utwórz nowe
                if not active_oco and base_balance > 0:
                    # Anuluj wszystkie istniejące zlecenia z obsługą błędów
                    for order in open_orders:
                        try:
                            client.cancel_order(symbol=symbol, orderId=order['orderId'])
                        except Exception as cancel_error:
                            if 'Unknown order sent' in str(cancel_error):
                                log_to_file(f"Zlecenie {order['orderId']} już nie istnieje, pomijam")
                                continue
                            raise
                    
                    # Utwórz nowe OCO z pełnym balansem
                    entry_price = float(signal['real_entry'])
                    stop_loss, take_profit = calculate_oco_levels(signal, entry_price)
                    
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
                        add_order_to_history(signal, oco_order, "OCO")
                        log_to_file(f"Utworzono nowe OCO dla {symbol}: SL={stop_loss}, TP={take_profit}")

                # Aktualizacja statusu OCO z dodatkowymi zabezpieczeniami
                if active_oco:
                    try:
                        order_status = get_order_details(symbol, active_oco['orderId'])
                        if order_status['status'] in ['FILLED', 'CANCELED']:
                            # Sprawdź który warunek został aktywowany
                            filled_order = next((o for o in signal["orders"] 
                                               if o['oco_group_id'] == active_oco['oco_group_id'] 
                                               and o['status'] == 'FILLED'), None)
                            
                            if filled_order:
                                signal.update({
                                    "status": "CLOSED",
                                    "exit_price": filled_order['avgPrice'],
                                    "exit_time": filled_order['time'],
                                    "exit_type": "TAKE_PROFIT" if filled_order['take_profit_price'] else "STOP_LOSS"
                                })
                    except KeyError as ke:
                        log_to_file(f"Błąd struktury zlecenia OCO: {ke}")
                        log_to_file(str(order_status))
                        #handle_critical_error(signal)

            except Exception as main_error:
                log_to_file(f"Krytyczny błąd zarządzania OCO: {main_error}")
                #handle_critical_error(signal)
            
            # Dynamiczna aktualizacja stop loss po osiągnięciu targetu
            if handle_targets(signal, current_price, base_balance):
                signal["status"] = "CLOSED"
            
        except Exception as e:
            log_to_file(f"Błąd podczas przetwarzania {symbol}: {e}")
            #handle_critical_error(signal)
            
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
    
    return adjust_price(signal['currency'], stop_loss), adjust_price(signal['currency'], take_profit)

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
            
            # Anuluj istniejące OCO
            for order in open_orders:
                client.cancel_order(symbol=symbol, orderId=order['orderId'])
            
            # Utwórz nowe OCO ze stop loss na entry price
            entry_price = float(signal['real_entry'])
            new_stop_loss = entry_price
            take_profit = targets[-1]
            
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
                add_order_to_history(signal, oco_order, "OCO")
                log_to_file(f"Zaktualizowano OCO po osiągnięciu targetu 1: SL={new_stop_loss}")
                
        except Exception as e:
            log_to_file(f"Błąd aktualizacji OCO: {e}")
            
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






