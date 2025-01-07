from binance.client import Client
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from dotenv import load_dotenv
import os, time
from datetime import datetime

def log_to_file(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("logfile.txt", "a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")

load_dotenv()
testmode = os.getenv('TESTMODE', 'false').lower() == 'true'
api_key = os.getenv('BINANCE_API_KEY')
api_secret = os.getenv('BINANCE_API_SECRET')

if testmode:
    client = Client(api_key, api_secret, testnet=True)
else:
    client = Client(api_key, api_secret)

def get_symbol_filters(symbol):
    """Pobiera i zwraca wszystkie filtry dla danego symbolu"""
    symbol_info = client.get_symbol_info(symbol)
    filters = {}
    for f in symbol_info['filters']:
        filters[f['filterType']] = f
    return filters

def adjust_quantity(symbol, quantity):
    """Dostosowuje ilość do wymogów LOT_SIZE"""
    filters = get_symbol_filters(symbol)
    lot_size = filters['LOT_SIZE']
    step_size = float(lot_size['stepSize'])
    min_qty = float(lot_size['minQty'])
    
    quantity = max(min_qty, quantity)
    quantity = round(quantity / step_size) * step_size
    return round(quantity, 8)

def adjust_price(symbol, price):
    """Dostosowuje cenę do wymogów PRICE_FILTER"""
    filters = get_symbol_filters(symbol)
    price_filter = filters['PRICE_FILTER']
    tick_size = float(price_filter['tickSize'])
    
    return round(round(price / tick_size) * tick_size, 8)

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

def log_order(order, order_type, symbol, quantity, price):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] Zlecenie {order_type} dla {symbol}: ID={order['orderId']}, Ilość={quantity}, Cena={price}"
    log_to_file(log_message)

def execute_trade(signal, percentage=20):
    symbol = signal["currency"]
    if has_open_position(symbol):
        log_to_file(f"Otwarta pozycja dla {symbol} już istnieje.")
        return False

    available_balance = get_available_balance("USDT")
    log_to_file(f"Stan konta USDT przed transakcją {available_balance}")
    
    quantity = calculate_trade_amount(available_balance, percentage, symbol)
    if quantity <= 0:
        log_to_file(f"Nieprawidłowa ilość dla {symbol}")
        return False

    side = SIDE_SELL if signal["signal_type"] == "SHORT" else SIDE_BUY
    quantity_per_target = adjust_quantity(symbol, quantity / len(signal["targets"]))

    try:
        # Zlecenie MARKET
        log_to_file(f"Składanie zlecenia MARKET: {symbol}, ilość={quantity}")
        market_order = client.create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )
        log_order(market_order, "MARKET", symbol, quantity, market_order['fills'][0]['price'])
        time.sleep(1)

        # Stop Loss
        stop_price = adjust_price(symbol, signal["stop_loss"])
        stop_loss_order = client.create_order(
            symbol=symbol,
            side=SIDE_BUY if side == SIDE_SELL else SIDE_SELL,
            type="STOP_LOSS_LIMIT",
            timeInForce="GTC",
            quantity=quantity,
            stopPrice=stop_price,
            price=stop_price
        )
        log_order(stop_loss_order, "STOP_LOSS", symbol, quantity, stop_price)
        time.sleep(1)

        # Take Profit orders
        for i, target in enumerate(signal["targets"]):
            target_price = adjust_price(symbol, target)
            take_profit_order = client.create_order(
                symbol=symbol,
                side=SIDE_BUY if side == SIDE_SELL else SIDE_SELL,
                type="TAKE_PROFIT_LIMIT",
                timeInForce="GTC",
                quantity=quantity_per_target,
                stopPrice=target_price,
                price=target_price
            )
            log_order(take_profit_order, f"TAKE_PROFIT_{i+1}", symbol, quantity_per_target, target_price)
            time.sleep(1)

        return True

    except Exception as e:
        log_to_file(f"Błąd podczas wykonywania transakcji: {str(e)}")
        return False
