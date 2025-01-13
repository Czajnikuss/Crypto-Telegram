from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from common import client, log_to_file, adjust_quantity, adjust_price, get_order_details, check_binance_pair_and_price
import time
from signal_history_manager import load_signal_history, save_signal_history

def get_available_balance(asset):
    try:
        balance = client.get_asset_balance(asset=asset)
        return float(balance['free'])
    except Exception as e:
        log_to_file(f"Błąd podczas pobierania salda: {e}")
        return 0.0

def calculate_trade_amount(available_balance, percentage, symbol):
    try:
        max_usdt = available_balance * (percentage / 100)
        ticker = client.get_symbol_ticker(symbol=symbol)
        current_price = float(ticker['price'])

        quantity = max_usdt / current_price
        quantity = adjust_quantity(symbol, quantity)

        actual_usdt_value = quantity * current_price

        if actual_usdt_value > max_usdt:
            quantity = adjust_quantity(symbol, (max_usdt * 0.99) / current_price)
            actual_usdt_value = quantity * current_price

        return quantity, actual_usdt_value, current_price
    except Exception as e:
        log_to_file(f"Błąd podczas obliczania kwoty transakcji: {e}")
        return 0.0, 0.0, 0.0

def has_open_position(symbol):
    try:
        positions = client.get_open_orders(symbol=symbol)
        return len(positions) > 0
    except Exception as e:
        log_to_file(f"Błąd podczas sprawdzania otwartych pozycji: {e}")
        return False

def add_order_to_history(signal: dict, order: dict, order_type: str) -> None:
    if "orders" not in signal:
        signal["orders"] = []
    
    full_order = get_order_details(order['symbol'], order['orderId'])
    
    if full_order:
        order_record = {
            "orderId": full_order['orderId'],
            "type": order_type,
            "status": full_order['status'],
            "stopPrice": float(full_order.get('stopPrice', 0)),
            "side": full_order['side'],
            "quantity": float(full_order['origQty']),
            "executedQty": float(full_order['executedQty']),
            "time": full_order['time']
        }
    else:
        order_record = {
            "orderId": order['orderId'],
            "type": order_type,
            "status": order.get('status', 'UNKNOWN'),
            "stopPrice": float(order.get('stopPrice', 0)),
            "side": order['side'],
            "quantity": float(order['origQty']),
            "executedQty": float(order.get('executedQty', 0)),
            "time": order.get('transactTime', int(time.time() * 1000))
        }
    
    signal["orders"].append(order_record)
    
    history = load_signal_history()
    updated = False
    for i, s in enumerate(history):
        if s["currency"] == signal["currency"] and s["date"] == signal["date"]:
            history[i] = signal
            updated = True
            break
    
    if not updated:
        history.append(signal)
    
    save_signal_history(history)

def execute_trade(signal, percentage=20):
    symbol = signal["currency"]
    result = check_binance_pair_and_price(client, symbol, signal['entry'])
    if result.get("error"):
        log_to_file(result["error"])
        if "status" not in signal:
            signal["status"] = "CLOSED"
        return False
    
    # Używamy symbolu z result, a nie ceny
    symbol = result['symbol']  # Zmiana z result['price'] na result['symbol']
    current_price = float(result['price'])
    
    if signal["signal_type"] != "LONG":
        log_to_file(f"Pomijam sygnał, ponieważ nie jest to LONG: {symbol}")
        if "status" not in signal:
            signal["status"] = "CLOSED"
        return False
    
        # Walidacja stop loss
    if signal["stop_loss"] < current_price * 0.7 or signal["stop_loss"] > current_price:  # Przyjmujemy 70% jako maksymalną stratę
        log_to_file(f"Stop loss {signal['stop_loss']} jest zbyt niski względem aktualnej ceny {current_price}")
        signal["stop_loss"] = current_price * 0.8  # Ustawiamy stop loss na 20% poniżej ceny

    try:
        # Sprawdzamy czy para istnieje w exchange info
        exchange_info = client.get_exchange_info()
        symbol_info = next((s for s in exchange_info['symbols'] if s['symbol'] == symbol), None)
        
        if not symbol_info:
            log_to_file(f"Para {symbol} nie istnieje na Binance")
            return False
            
        if has_open_position(symbol):
            log_to_file(f"Otwarta pozycja dla {symbol} już istnieje.")
            return False

        available_balance = get_available_balance("USDT")
        log_to_file(f"Stan konta USDT przed transakcją: {available_balance}")
        
        quantity, usdt_value, current_price = calculate_trade_amount(
            available_balance, 
            percentage, 
            symbol
        )
        
        if quantity <= 0:
            log_to_file(f"Nie można obliczyć prawidłowej wielkości zlecenia dla {symbol}")
            return False

        # Market order
        log_to_file(f"Rozpoczynam składanie zlecenia MARKET dla {symbol}:")
        log_to_file(f"Ilość: {quantity}, Strona: BUY, Wartość USDT: {usdt_value:.2f}, Cena: {current_price}")
        
        market_order = client.create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )
        
        time.sleep(1)
        add_order_to_history(signal, market_order, "MARKET")
        time.sleep(1)

        # Stop Loss
        stop_price = adjust_price(symbol, signal["stop_loss"])
        log_to_file(f"Rozpoczynam składanie zlecenia STOP_LOSS_LIMIT dla {symbol}:")
        log_to_file(f"Ilość: {quantity}, Strona: SELL, Cena stop: {stop_price}")
        
        stop_loss_order = client.create_order(
            symbol=symbol,
            side=SIDE_SELL,
            type="STOP_LOSS_LIMIT",
            timeInForce="GTC",
            quantity=quantity,
            stopPrice=stop_price,
            price=stop_price
        )
        add_order_to_history(signal, stop_loss_order, "STOP_LOSS")
        
        return True

    except Exception as e:
        log_to_file(f"Błąd podczas wykonywania transakcji: {str(e)}")
        log_to_file(f"Pełny kontekst błędu: {e.__dict__}")
        return False


test_signal =    {
        "currency": "USUALUSDT",
        "signal_type": "LONG",
        "entry": 0.5114,
        "targets": [
            0.531,
            0.549,
            0.563,
            0.578
        ],
        "stop_loss": 0.0005,
        "breakeven": 0.5114,
        "date": "2025-01-13T13:57:37+00:00"
    }
#execute_trade(test_signal, 5)

