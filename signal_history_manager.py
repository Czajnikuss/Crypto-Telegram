import json
import os, time
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

def update_signal_orders(signal):
    """Aktualizuje stan zleceĹ„ w sygnale"""
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
                "time": order['time']
            }
            for order in orders if order is not None
        ]
        return signal
    except Exception as e:
        log_to_file(f"BĹ‚Ä…d podczas aktualizacji zleceĹ„ dla {symbol}: {e}")
        return signal

def handle_critical_error(signal):
    symbol = signal["currency"]
    try:
        # Anuluj wszystkie otwarte zlecenia
        open_orders = client.get_open_orders(symbol=symbol)
        for order in open_orders:
            try:
                client.cancel_order(symbol=symbol, orderId=order['orderId'])
                log_to_file(f"Anulowano zlecenie {order['orderId']} dla {symbol}")
            except:
                continue

        # Zamknij pozycjÄ™ jeĹ›li istnieje
        account = client.get_account()
        symbol_info = client.get_symbol_info(symbol)
        base_asset = symbol_info['baseAsset']
        base_balance = float(next((b['free'] for b in account['balances'] if b['asset'] == base_asset), 0))

        if base_balance > 0:
            closing_side = 'BUY' if signal['signal_type'] == 'SHORT' else 'SELL'
            adjusted_quantity = adjust_quantity(symbol, base_balance)
            
            if adjusted_quantity > 0:
                client.create_order(
                    symbol=symbol,
                    side=closing_side,
                    type='MARKET',
                    quantity=adjusted_quantity
                )
                log_to_file(f"Awaryjne zamkniÄ™cie pozycji dla {symbol}, iloĹ›Ä‡: {adjusted_quantity}")

        signal["status"] = "CLOSED"
        signal["error"] = "CRITICAL_ERROR"
        
    except Exception as e:
        log_to_file(f"BĹ‚Ä…d podczas obsĹ‚ugi sytuacji krytycznej dla {symbol}: {e}")

def check_and_update_signal_history():
    history = load_signal_history()
    for signal in history:
        if signal.get("status") != "CLOSED":
            try:
                signal = update_signal_orders(signal)
                symbol =signal["currency"]
                
                if not signal.get("orders"):
                    log_to_file(f"no oreders for signal {symbol}")
                    handle_critical_error(signal)
                    continue

                market_orders = [o for o in signal["orders"] if o['type'] == 'MARKET' and o['status'] == 'FILLED']
                stop_loss_orders = [o for o in signal["orders"] if o['type'] == 'STOP_LOSS_LIMIT']
                take_profit_orders = [o for o in signal["orders"] if o['type'] == 'TAKE_PROFIT_LIMIT']

                if not market_orders or not stop_loss_orders:
                    log_to_file(f"no oreders Stop loss or market orders for signal {symbol}")
                    handle_critical_error(signal)
                    continue

                if any(o['status'] == 'FILLED' for o in stop_loss_orders):
                    signal["status"] = "CLOSED"
                    continue

                filled_take_profits = [o for o in take_profit_orders if o['status'] == 'FILLED']
                if len(filled_take_profits) == len(signal["targets"]):
                    signal["status"] = "CLOSED"
                elif filled_take_profits:
                    new_stop = adjust_price(symbol, signal["breakeven"])
                    for order in stop_loss_orders:
                        if order['status'] == 'NEW':
                            try:
                                client.cancel_order(symbol=symbol, orderId=order['orderId'])
                                time.sleep(1)
                                client.create_order(
                                    symbol=symbol,
                                    side=order['side'],
                                    type="STOP_LOSS_LIMIT",
                                    timeInForce="GTC",
                                    quantity=float(order['origQty']),
                                    stopPrice=new_stop,
                                    price=new_stop
                                )
                            except Exception as e:
                                log_to_file(f"BĹ‚Ä…d aktualizacji stop-loss: {e}")

            except Exception as e:
                log_to_file(f"BĹ‚Ä…d podczas sprawdzania historii dla {signal['currency']}: {e}")

    save_signal_history(history)
