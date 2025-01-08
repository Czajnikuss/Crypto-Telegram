from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from common import client, log_to_file, adjust_quantity, adjust_price, get_order_details
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
        trade_amount_usdt = available_balance * (percentage / 100)
        ticker = client.get_symbol_ticker(symbol=symbol)
        current_price = float(ticker['price'])
        quantity = trade_amount_usdt / current_price
        return adjust_quantity(symbol, quantity)
    except Exception as e:
        log_to_file(f"Błąd podczas obliczania kwoty transakcji: {e}")
        return 0.0

def has_open_position(symbol):
    try:
        positions = client.get_open_orders(symbol=symbol)
        return len(positions) > 0
    except Exception as e:
        log_to_file(f"Błąd podczas sprawdzania otwartych pozycji: {e}")
        return False

def add_order_to_history(signal: dict, order: dict, order_type: str) -> None:
    """Dodaje zlecenie do historii sygnału z obsługą błędów"""
    if "orders" not in signal:
        signal["orders"] = []
    
    # Czekaj na przetworzenie zlecenia
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
        # Jeśli nie można pobrać szczegółów, użyj danych z oryginalnego zlecenia
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
    
    # Aktualizuj historię
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
    if has_open_position(symbol):
        log_to_file(f"Otwarta pozycja dla {symbol} już istnieje.")
        return False

    available_balance = get_available_balance("USDT")
    log_to_file(f"Stan konta USDT przed transakcją {available_balance}")
    
    quantity = calculate_trade_amount(available_balance, percentage, symbol)
    if quantity <= 0:
        return False

    side = SIDE_SELL if signal["signal_type"] == "SHORT" else SIDE_BUY
    quantity_per_target = adjust_quantity(symbol, quantity / len(signal["targets"]))

    try:
        # Market order
        log_to_file(f"Rozpoczynam składanie zlecenia MARKET dla {symbol}: ilość={quantity}, side={side}")
        market_order = client.create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )
        time.sleep(1)
        add_order_to_history(signal, market_order, "MARKET")
        time.sleep(1)

        # Stop Loss
        
        stop_price = adjust_price(symbol, signal["stop_loss"])
        log_to_file(f"Rozpoczynam składanie zlecenia STOP_LOSS_LIMIT dla {symbol}: ilość={quantity}, side={side} price={stop_price}")
        stop_loss_order = client.create_order(
            symbol=symbol,
            side=SIDE_BUY if side == SIDE_SELL else SIDE_SELL,
            type="STOP_LOSS_LIMIT",
            timeInForce="GTC",
            quantity=quantity,
            stopPrice=stop_price,
            price=stop_price
        )
        add_order_to_history(signal, stop_loss_order, "STOP_LOSS")
        time.sleep(1)
        """
        # Take Profit orders
        for target in signal["targets"]:
            target_price = adjust_price(symbol, target)
            log_to_file(f"Rozpoczynam składanie zlecenia TAKE_PROFIT_LIMI dla {symbol}: ilość={quantity_per_target}, side={side} price={target_price}")
            take_profit_order = client.create_order(
                symbol=symbol,
                side=SIDE_BUY if side == SIDE_SELL else SIDE_SELL,
                type="TAKE_PROFIT_LIMIT",
                timeInForce="GTC",
                quantity=quantity_per_target,
                stopPrice=target_price,
                price=target_price
            )
            add_order_to_history(signal, take_profit_order, "TAKE_PROFIT")
            time.sleep(1)

        return True
        """

    except Exception as e:
        log_to_file(f"Błąd podczas wykonywania transakcji: {str(e)}")
        log_to_file(f"Pełny kontekst błędu: {e.__dict__}")
        return False


    
test_signal = {
        "currency": "MASKUSDT",
        "signal_type": "LONG",
        "entry": 3.0,
        "targets": [
            3.1,
            3.196,
            3.3,
            3.373
        ],
        "stop_loss": 2.788,
        "breakeven": 2.9,
        "date": "2025-01-07T15:00:28+00:00"
}
#execute_trade(test_signal, 5)
