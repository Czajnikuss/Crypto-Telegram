import json
import os, time
from datetime import datetime
from binance_trading import client, log_to_file, adjust_quantity, adjust_price

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
        # Anuluj wszystkie otwarte zlecenia
        open_orders = client.get_open_orders(symbol=symbol)
        for order in open_orders:
            try:
                client.cancel_order(symbol=symbol, orderId=order['orderId'])
                log_to_file(f"Anulowano zlecenie {order['orderId']} dla {symbol}")
            except:
                continue

        # Zamknij pozycję jeśli istnieje
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
                log_to_file(f"Awaryjne zamknięcie pozycji {symbol}, ilość: {adjusted_quantity}")

        signal["status"] = "CLOSED"
        signal["error"] = "CRITICAL_ERROR"
        
    except Exception as e:
        log_to_file(f"Błąd podczas obsługi sytuacji krytycznej dla {symbol}: {e}")

def check_and_update_signal_history():
    history = load_signal_history()
    for signal in history:
        if signal.get("status") != "CLOSED":
            try:
                symbol = signal["currency"]
                orders = client.get_all_orders(symbol=symbol, limit=50)
                
                if not orders:
                    handle_critical_error(signal)
                    continue

                market_orders = [o for o in orders if o['type'] == 'MARKET' and o['status'] == 'FILLED']
                stop_loss_orders = [o for o in orders if o['type'] in ['STOP_LOSS', 'STOP_LOSS_LIMIT']]
                take_profit_orders = [o for o in orders if o['type'] in ['TAKE_PROFIT', 'TAKE_PROFIT_LIMIT']]

                if not market_orders or not stop_loss_orders:
                    handle_critical_error(signal)
                    continue

                # Aktualizuj status
                if any(o['status'] == 'FILLED' for o in stop_loss_orders):
                    signal["status"] = "CLOSED"
                    continue

                filled_take_profits = [o for o in take_profit_orders if o['status'] == 'FILLED']
                if filled_take_profits:
                    if len(filled_take_profits) == len(signal["targets"]):
                        signal["status"] = "CLOSED"
                    else:
                        # Aktualizuj stop-loss do breakeven
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
                                    log_to_file(f"Błąd aktualizacji stop-loss: {e}")

            except Exception as e:
                log_to_file(f"Błąd podczas sprawdzania historii dla {signal['currency']}: {e}")

    save_signal_history(history)
