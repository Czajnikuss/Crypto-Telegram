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
        min_notional = get_min_notional(symbol)
        
        ticker = client.get_symbol_ticker(symbol=symbol)
        current_price = float(ticker['price'])
        current_price = adjust_price(symbol, current_price)  # Używamy adjust_price
        
        quantity = max_usdt / current_price
        quantity = adjust_quantity(symbol, quantity)  # Używamy adjust_quantity
        
        actual_value = quantity * current_price
        
        # Sprawdzamy min_notional tylko jeśli jest większe od 0
        if min_notional > 0 and actual_value < min_notional:
            required_qty = (min_notional * 1.01) / current_price  # Dodajemy 1% marginesu
            quantity = adjust_quantity(symbol, required_qty)
            actual_value = quantity * current_price
            if actual_value < min_notional:  # Jeśli nadal za mało
                log_to_file(f"Wartość zlecenia ({actual_value} USDT) jest poniżej minimum ({min_notional} USDT)")
                return 0.0, 0.0, 0.0
        
        return quantity, actual_value, current_price
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
    
def get_min_notional(symbol):
    info = client.get_symbol_info(symbol)
    for f in info['filters']:
        if f['filterType'] == 'NOTIONAL':
            return float(f['minNotional'])
        elif f['filterType'] == 'MIN_NOTIONAL':  # dla kompatybilności wstecznej
            return float(f['minNotional'])
    return 0.0  # jeśli nie znaleziono żadnego filtru notional, pozwalamy na handel


def execute_trade(signal, percentage=20):
    try:
        symbol = signal["currency"]
        log_to_file(f"Rozpoczynam przetwarzanie sygnału dla {symbol}")
        
        # Dodajemy explicit logowanie przed każdą operacją API
        log_to_file("Pobieram exchange_info...")
        exchange_info = client.get_exchange_info()
        
        log_to_file("Szukam informacji o symbolu...")
        symbol_info = next((s for s in exchange_info['symbols'] if s['symbol'] == symbol), None)
        
        log_to_file("wykonuję walidację pary handlowej")
        validation_result = check_binance_pair_and_price(client, symbol, signal["entry"])
        if "error" in validation_result:
            log_to_file(f"Walidacja pary nie powiodła się: {validation_result['error']}")
            return False
        
        
        if not symbol_info:
            log_to_file(f"Para {symbol} nie istnieje na Binance")
            return False
        log_to_file("Dodatkowe potwierdzenie że symbol istnieje na Binance")
            
        if has_open_position(symbol):
            log_to_file(f"Otwarta pozycja dla {symbol} już istnieje")
            return False
        log_to_file("potwierdziłem brak otwartych pozycji")
            
        if signal["signal_type"] != "LONG":
            log_to_file(f"Pomijam sygnał, ponieważ nie jest to LONG: {symbol}")
            return False
        log_to_file("Sygnał jest typu LONG")
            
        # 2. Pobranie parametrów handlowych
        filters = {f['filterType']: f for f in symbol_info['filters']}
        tick_size = float(filters['PRICE_FILTER']['tickSize'])
        lot_size = float(filters['LOT_SIZE']['stepSize'])
        
        # Używamy nowej funkcji get_min_notional zamiast bezpośredniego dostępu
        min_notional = get_min_notional(symbol)
        log_to_file(f"Uzyskałęm filtry dla symbolu: {symbol}")
        
        ticker = client.get_symbol_ticker(symbol=symbol)
        current_price = float(ticker['price'])
        log_to_file(f"Uzyskałęm ticker dla symbolu: {symbol}, aktualna cena: {ticker['price']}")
        
        # Walidacja stop loss
        if signal["stop_loss"] < current_price * 0.7 or signal["stop_loss"] > current_price:
            log_to_file(f"Stop loss {signal['stop_loss']} jest nieprawidłowy względem ceny {current_price}")
            return False
            
        # 3. Kalkulacja wielkości zlecenia
        available_balance = get_available_balance("USDT")
        log_to_file(f"Stan konta USDT przed transakcją: {available_balance}")
        
       
        max_usdt = available_balance * (percentage / 100) * 0.998
        quantity = max_usdt / current_price
        quantity = adjust_quantity(symbol, quantity)  # Używamy funkcji z common.py
        actual_value = quantity * current_price
        
        # Jeśli min_notional > 0, sprawdzamy warunek
        if min_notional > 0 and actual_value < min_notional:
            log_to_file(f"Wartość zlecenia ({actual_value} USDT) poniżej minimum ({min_notional} USDT)")
            return False

        if actual_value < min_notional:
            required_qty = min_notional / current_price * 1.01  # Dodajemy 1% marginesu
            quantity = adjust_quantity(symbol, required_qty)
            actual_value = quantity * current_price

        
        if actual_value < min_notional:
            log_to_file(f"Wartość zlecenia ({actual_value} USDT) poniżej minimum ({min_notional} USDT)")
            return False
            
        # 4. Realizacja MARKET
        log_to_file(f"Składanie zlecenia MARKET dla {symbol}:")
        log_to_file(f"Ilość: {quantity}, Strona: BUY, Wartość USDT: {actual_value:.2f}, Cena: {current_price}")
        log_to_file(f"Wymagane minimum (MIN_NOTIONAL): {min_notional} USDT")

        
        market_order = client.create_order(
            symbol=symbol,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )
        
        if market_order.get('status') != 'FILLED':
            log_to_file(f"Zlecenie MARKET nie zostało zrealizowane. Status: {market_order.get('status')}")
            return False
            
        executed_qty = float(market_order['executedQty'])
        log_to_file(f"Zlecenie MARKET zrealizowane. Kupiono: {executed_qty}")
        add_order_to_history(signal, market_order, "MARKET")
        
        # 5. Realizacja STOP_LOSS
        time.sleep(2)
        
        currency = symbol.replace('USDT', '')
        currency_balance = get_available_balance(currency)
        log_to_file(f"Dostępne {currency}: {currency_balance}")
        
        if currency_balance < executed_qty:
            log_to_file(f"Niedostateczne saldo {currency} do złożenia STOP_LOSS")
            return False
            
        stop_price = float(round(signal["stop_loss"] / tick_size) * tick_size)
        stop_price = adjust_price(symbol, stop_price)
        limit_price = adjust_price(symbol, stop_price * 0.995)

        
        log_to_file(f"Składanie zlecenia STOP_LOSS_LIMIT dla {symbol}:")
        log_to_file(f"Ilość: {executed_qty}, Stop: {stop_price}, Limit: {limit_price}")
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                stop_loss_order = client.create_order(
                    symbol=symbol,
                    side=SIDE_SELL,
                    type="STOP_LOSS_LIMIT",
                    timeInForce="GTC",
                    quantity=executed_qty,
                    stopPrice=stop_price,
                    price=limit_price
                )
                if stop_loss_order and stop_loss_order.get('status') == 'NEW':
                    break
                time.sleep(2 ** attempt)
            except Exception as e:
                if attempt == max_retries - 1:
                    log_to_file(f"Wszystkie próby utworzenia STOP_LOSS_LIMIT nieudane: {str(e)}")
                    return False
                continue
            
        log_to_file(f"STOP_LOSS_LIMIT aktywowany pomyślnie")
        add_order_to_history(signal, stop_loss_order, "STOP_LOSS")
        return True
        
    except Exception as e:
        log_to_file(f"Błąd wykonania transakcji: {str(e)}")
        log_to_file(f"Kontekst błędu: {e.__dict__}")
        return False




test_signal =    {
        
        "currency": "DOGEUSDT",
        "signal_type": "LONG",
        "entry": 0.41124,
        "targets": [
            0.41946,
            0.42358,
            0.42769,
            0.4318
        ],
        "stop_loss": 0.39479,
        "breakeven": 0.41124,
        "date": "2025-01-17T12:00:17+00:00",
        "highest_price": 0.41274,
    }
execute_trade(test_signal, 2)

