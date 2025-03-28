from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from common import client, log_to_file, adjust_quantity, adjust_price, get_order_details, check_binance_pair_and_price, create_oco_order_direct
import time, math, json
from signal_history_manager import load_signal_history, save_signal_history
import traceback


def get_available_balance(asset):
    try:
        balance = client.get_asset_balance(asset=asset)
        return float(balance['free'])
    except Exception as e:
        log_to_file(f"Błąd podczas pobierania salda: {e}")
        return 0.0


def check_price_condition(current_price, signal):
    targets = signal["targets"]
    
    
    if current_price < targets[0]:
        return True
    else:
        return "Błąd: Aktualna cena jest powyżej pierwszego celu"
    

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
    
    if 'orderReports' in order:
        for report in order.get('orderReports', []):
            try:
                executed_qty = float(report.get('executedQty', 0))
                cum_quote_qty = float(report.get('cummulativeQuoteQty', 0))
                
                order_record = {
                    "orderId": report['orderId'],
                    "type": report['type'],
                    "status": report['status'],
                    "side": report['side'],
                    "quantity": float(report['origQty']),
                    "executedQty": executed_qty,
                    "avgPrice": cum_quote_qty / executed_qty if executed_qty > 0 else 0,
                    "time": report.get('transactTime', int(time.time() * 1000)),
                    "price": float(report.get('price', 0)),
                    "stopPrice": float(report.get('stopPrice', 0)),
                    "take_profit_price": float(report['price']) if report['type'] == 'LIMIT_MAKER' else None,
                    "stop_loss_trigger": float(report.get('stopPrice', 0)) if report['type'] == 'STOP_LOSS_LIMIT' else None,
                    "stop_loss_limit": float(report['price']) if report['type'] == 'STOP_LOSS_LIMIT' else None,
                    "oco_group_id": order.get('orderListId', report.get('orderListId'))
                }
                signal["orders"].append(order_record)
            except KeyError as ke:
                log_to_file(f"Błąd przetwarzania raportu: {ke}")
    else:
        full_order = get_order_details(order['symbol'], order['orderId']) if 'orderId' in order else None
        
        if full_order:
            executed_qty = float(full_order.get('executedQty', 0))
            cum_quote_qty = float(full_order.get('cummulativeQuoteQty', 0))
            
            order_record = {
                "orderId": full_order['orderId'],
                "type": order_type,
                "status": full_order['status'],
                "stopPrice": float(full_order.get('stopPrice', 0)),
                "side": full_order['side'],
                "quantity": float(full_order['origQty']),
                "executedQty": executed_qty,
                "avgPrice": cum_quote_qty / executed_qty if executed_qty > 0 else 0,
                "time": full_order.get('transactTime', int(time.time() * 1000))
            }
        else:
            executed_qty = float(order.get('executedQty', 0))
            cum_quote_qty = float(order.get('cummulativeQuoteQty', 0))
            
            order_record = {
                "orderId": order.get('orderId'),
                "type": order_type,
                "status": order.get('status', 'UNKNOWN'),
                "stopPrice": float(order.get('stopPrice', 0)),
                "side": order.get('side'),
                "quantity": float(order.get('origQty', 0)),
                "executedQty": executed_qty,
                "avgPrice": cum_quote_qty / executed_qty if executed_qty > 0 else 0,
                "time": order.get('transactTime', int(time.time() * 1000)),
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
        log_to_file(f"Rozpoczynam przetwarzanie sygnału:\n{json.dumps(signal, indent=2)}")
        log_to_file(f"Procent kapitału: {percentage}%")
        
        # Dodajemy explicit logowanie przed każdą operacją API
        log_to_file("Pobieram exchange_info...")
        exchange_info = client.get_exchange_info()
        
        log_to_file("Szukam informacji o symbolu...")
        symbol_info = next((s for s in exchange_info['symbols'] if s['symbol'] == symbol), None)
        
        log_to_file("wykonuję walidację pary handlowej")
        validation_result = check_binance_pair_and_price(client, symbol, signal["entry"])
        if "error" in validation_result:
            log_to_file(f"Walidacja pary nie powiodła się: {validation_result['error']}")
            signal["status"] = "CLOSED"
            signal["error"] = f"Walidacja pary nie powiodła się: {validation_result['error']}"
            return False
        
        
        if not symbol_info:
            log_to_file(f"Para {symbol} nie istnieje na Binance")
            signal["status"] = "CLOSED"
            signal["error"] = f"Para {symbol} nie istnieje na Binance"
            return False
        log_to_file("Dodatkowe potwierdzenie że symbol istnieje na Binance")
            
        if has_open_position(symbol):
            log_to_file(f"Otwarta pozycja dla {symbol} już istnieje")
            signal["status"] = "CLOSED"
            signal["error"] = f"Otwarta pozycja dla {symbol} już istnieje"
            return False
        log_to_file("potwierdziłem brak otwartych pozycji")
            
        if signal["signal_type"] != "LONG":
            log_to_file(f"Pomijam sygnał, ponieważ nie jest to LONG: {symbol}")
            signal["status"] = "CLOSED"
            signal["error"] = f"Pomijam sygnał, ponieważ nie jest to LONG: {symbol}"
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
        
        #waildacja czy sygnał nie jest przedwczesny dla rynku, anulujemy jeśłi 2 cel został zrealizowany.
        if check_price_condition(current_price, signal) is True:
            log_to_file(f"Aktualna cena poniżej 1 go celu")
        else:
            log_to_file(f"Warunek niespełniony: {check_price_condition(current_price, signal)}")
            signal["status"] = "CLOSED"
            signal["error"] = "Cena na rynku za wysoka na wejście."
            return False
        
        # Walidacja stop loss
        if signal["stop_loss"] < current_price * 0.8 or signal["stop_loss"] > current_price:
            log_to_file(f"Stop loss {signal['stop_loss']} jest nieprawidłowy względem ceny {current_price}")
            signal["stop_loss"] = current_price * 0.85
            log_to_file(f"Skorygowano stop loss do poziomu {signal['stop_loss']}")
            
            
        # 3. Kalkulacja wielkości zlecenia
        available_balance = get_available_balance("USDT")
        log_to_file(f"Stan konta USDT przed transakcją: {available_balance}")

        currency = symbol.replace('USDT', '')
        initial_currency_balance = get_available_balance(currency)
        log_to_file(f"Początkowe saldo {currency}: {initial_currency_balance}")

        # Dodajemy zabezpieczenie przed zerowymi wartościami
        if current_price <= 0:
            log_to_file(f"Błędna cena rynkowa: {current_price}")
            return False

        max_usdt = available_balance * (percentage / 100) * 0.998
        quantity = max_usdt / current_price
        quantity = adjust_quantity(symbol, quantity)
        actual_value = quantity * current_price

        # Dodajemy walidację quantity
        if quantity <= 0:
            log_to_file(f"Błędna kalkulacja ilości: {quantity}")
            return False

        # Jeśli min_notional > 0, sprawdzamy warunek
        if min_notional > 0 and actual_value < min_notional:
            log_to_file(f"Wartość zlecenia ({actual_value} USDT) poniżej minimum ({min_notional} USDT)")
            signal["status"] = "CLOSED"
            signal["error"] = f"Wartość zlecenia ({actual_value} USDT) poniżej minimum ({min_notional} USDT)"
            return False

        if actual_value < min_notional:
            required_qty = min_notional / current_price * 1.01  # Dodajemy 1% marginesu
            quantity = adjust_quantity(symbol, required_qty)
            actual_value = quantity * current_price


        # 4. Realizacja MARKET
        log_to_file(f"Składanie zlecenia MARKET dla {symbol}:")
        log_to_file(f"Ilość: {quantity}, Strona: BUY, Wartość USDT: {actual_value:.2f}, Cena: {current_price}")
        log_to_file(f"Wymagane minimum (MIN_NOTIONAL): {min_notional} USDT")

        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                market_order = client.create_order(
                    symbol=symbol,
                    side=SIDE_BUY,
                    type=ORDER_TYPE_MARKET,
                    quantity=quantity
                )
                if market_order.get('status') == 'FILLED':
                    break
                time.sleep(2 ** attempt)  # exponential backoff
            except Exception as e:
                if attempt == max_retries - 1:
                    log_to_file(f"Wszystkie próby wykonania zlecenia MARKET nieudane: {str(e)}")
                    signal["status"] = "CLOSED"
                    signal["error"] = f"Wszystkie próby wykonania zlecenia MARKET nieudane: {str(e)}"
                    return False
                continue
        
        time.sleep(2)  # Czekamy na aktualizację salda
        final_balance = get_available_balance(currency)
        balance_diff = final_balance - initial_currency_balance
        
        if balance_diff > 0:
            avg_price = float(market_order.get('cummulativeQuoteQty', 0)) / balance_diff
            log_to_file(f"Zlecenie MARKET zrealizowane. Kupiono: {balance_diff} po średniej cenie: {avg_price}")
            
            #Dodajemy real amount do sygnału
            signal["real_amount"] = balance_diff
            # Dodajemy real_entry do sygnału
            signal["real_entry"] = avg_price
            if avg_price <= 0:
                log_to_file(f"Błędna cena rynkowa zakupu: {avg_price}")
                signal["status"] = "CLOSED"
                signal["error"] = f"Błędna cena rynkowa zakupu: {avg_price}"
                return False
            add_order_to_history(signal, market_order, "MARKET")
        else:
            log_to_file(f"Zlecenie MARKET nie powiodło się. Status: {market_order.get('status')}")
            log_to_file(f"Brak zmiany salda {currency}: {initial_currency_balance} -> {final_balance}")
            signal["status"] = "CLOSED"
            signal["error"] = f"Brak zmiany salda {currency}: {initial_currency_balance} -> {final_balance}"
            return False    
        
        add_order_to_history(signal, market_order, "MARKET")
        
        # 5. Realizacja OCO
        time.sleep(2)

        currency = symbol.replace('USDT', '')
        currency_balance = get_available_balance(currency)
        log_to_file(f"Dostępne {currency}: {currency_balance}")

        stop_loss_qty = min(balance_diff, currency_balance)
        oco_qty = adjust_quantity(symbol, stop_loss_qty * 0.998) 
        log_to_file(f"Użycie stop_loss_qty dla STOP_LOSS: {stop_loss_qty}")

        stop_price = float(round(signal["stop_loss"] / tick_size) * tick_size)
        stop_price = adjust_price(symbol, stop_price)
        stop_limit_price = adjust_price(symbol, stop_price * 0.995)

        # Wybór poziomu take profit (2gi target jeśli istnieje, jeśli nie to 1szy)
        take_profit_price = float(signal["targets"][1] if len(signal["targets"]) > 1 else signal["targets"][0])
        take_profit_price = adjust_price(symbol, take_profit_price)         

        log_to_file(f"Składanie zlecenia OCO dla {symbol}:")
        log_to_file(f"Ilość: {oco_qty}")
        log_to_file(f"Stop Price: {stop_price}, Stop Limit: {stop_limit_price}")
        log_to_file(f"Take Profit: {take_profit_price}")

        # W sekcji realizacji OCO:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                oco_order = create_oco_order_direct(
                    client=client,
                    symbol=symbol,
                    side=SIDE_SELL,
                    quantity=oco_qty,
                    take_profit_price=take_profit_price,
                    stop_price=stop_price,
                    stop_limit_price=stop_limit_price
                )
                
                if oco_order and 'orderListId' in oco_order:
                    log_to_file("OCO order aktywowany pomyślnie")
                    signal["oco_order_id"] = oco_order['orderListId']
                    signal["status"] = "OPEN"
                    add_order_to_history(signal, oco_order, "OCO_ORDER")
                    return True  # Zmiana: natychmiastowy return po sukcesie
                else:
                     log_to_file(f"Nie udało się utworzyć OCO zlecenia, odpowiedź: {oco_order}")
                    
            except Exception as e:
                if attempt == max_retries - 1:
                    log_to_file(f"Wszystkie próby utworzenia OCO nieudane: {str(e)}")
                    signal["error"] = f"Wszystkie próby utworzenia OCO nieudane: {str(e)}"
                    return False

                
                continue
                        
        return True
        
    except Exception as e:
        error_trace = traceback.format_exc()
        log_to_file(f"Stack trace:\n{error_trace}")
        log_to_file(f"Błąd wykonania transakcji: {str(e)}")
        log_to_file(f"Kontekst błędu: {e.__dict__}")
        log_to_file(f"Stack trace:\n{error_trace}")
        return False
    
    finally:
        # Aktualizacja historii sygnałów
        history = load_signal_history()
        
        # Znajdujemy i nadpisujemy cały sygnał w historii
        for idx, hist_signal in enumerate(history):
            if (hist_signal["currency"] == signal["currency"] and 
                hist_signal["date"] == signal["date"]):
                history[idx] = signal
                break
                
        save_signal_history(history)




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
#execute_trade(test_signal, 2)

