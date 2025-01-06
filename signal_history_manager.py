import json
import os, time 
from binance_trading import client
from datetime import datetime

SIGNAL_HISTORY_FILE = 'signal_history.json'

def log_to_file(message):
    """
    Zapisuje wiadomość do pliku logfile.txt z timestampem.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("logfile.txt", "a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")

def load_signal_history():
    """
    Ładuje historię sygnałów z pliku JSON.
    """
    if os.path.exists(SIGNAL_HISTORY_FILE):
        with open(SIGNAL_HISTORY_FILE, 'r') as file:
            return json.load(file)
    return []

def save_signal_history(history):
    """
    Zapisuje historię sygnałów do pliku JSON.
    """
    with open(SIGNAL_HISTORY_FILE, 'w') as file:
        json.dump(history, file, indent=4)

def get_orders_for_signal(signal):
    """
    Pobiera wszystkie zlecenia (otwarte i zamknięte) dla danego sygnału z Binance i aktualizuje strukturę `orders`.
    Dodaje tylko zlecenia złożone po zleceniu MARKET najbliższym cenie entry.
    """
    symbol = signal["currency"]
    entry_price = signal["entry"]
    try:
        # Pobierz wszystkie zlecenia (otwarte i zamknięte)
        all_orders = client.get_all_orders(symbol=symbol, limit=100)

        # Znajdź zlecenia MARKET
        market_orders = [
            order for order in all_orders 
            if order['type'] == 'MARKET' and order['status'] == 'FILLED'
        ]

        if not market_orders:
            raise ValueError(f"Brak zleceń MARKET dla {symbol}")

        # Znajdź zlecenie MARKET z ceną najbliższą entry
        closest_market_order = min(
            market_orders,
            key=lambda order: abs(float(order.get('cummulativeQuoteQty', 0)) / float(order['executedQty']) - entry_price)
        )

        # Filtruj zlecenia złożone po znalezionym zleceniu MARKET
        signal["orders"] = [
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
            for order in all_orders
            if order['time'] >= closest_market_order['time']
        ]

    except Exception as e:
        log_to_file(f"Błąd podczas pobierania zleceń dla {symbol}: {e}")
    
    return signal


def update_achieved_targets(signal, target_price):
    """
    Aktualizuje listę osiągniętych celów.
    """
    for i, target in enumerate(signal["targets"]):
        if abs(target - target_price) / target <= 0.001:  # Tolerancja 0.1%
            if i not in signal["achieved_targets"]:
                signal["achieved_targets"].append(i)
                log_to_file(f"Osiągnięto cel {i + 1} dla {signal['currency']}.")
                break


def cancel_remaining_orders(signal):
    """
    Anuluje pozostałe zlecenia take-profit dla danego sygnału.
    """
    symbol = signal["currency"]
    try:
        open_orders = client.get_open_orders(symbol=symbol)
        for order in open_orders:
            if order['type'] == 'TAKE_PROFIT':
                client.cancel_order(symbol=symbol, orderId=order['orderId'])
                log_to_file(f"Anulowano zlecenie take-profit {order['orderId']} dla {symbol}.")
    except Exception as e:
        log_to_file(f"Błąd podczas anulowania zleceń take-profit dla {symbol}: {e}")
        
def handle_critical_error(signal):
    """
    Obsługuje krytyczne błędy w strukturze zleceń sygnału.
    """
    symbol = signal["currency"]
    try:
        # Pobierz dane do analizy
        all_orders = client.get_all_orders(symbol=symbol, limit=100)
        open_orders = client.get_open_orders(symbol=symbol)
        
        # Sprawdź logi z ostatnich 5 minut
        #recent_logs = search_logs_for_symbol(symbol, minutes=5)
        
        # Sprawdź stan konta dla danej waluty
        account = client.get_account()
        symbol_info = client.get_symbol_info(symbol)
        base_asset = symbol_info['baseAsset']
        quote_asset = symbol_info['quoteAsset']
        balances = {asset['asset']: float(asset['free']) for asset in account['balances']}
        
        # Anuluj wszystkie otwarte zlecenia
        for order in open_orders:
            try:
                client.cancel_order(symbol=symbol, orderId=order['orderId'])
                log_to_file(f"Anulowano zlecenie {order['orderId']} dla {symbol}")
            except:
                continue

        # Jeśli mamy otwartą pozycję, zamknij ją
        base_balance = balances.get(base_asset, 0)
        if base_balance > 0:
            try:
                # Dostosuj ilość do zasad LOT_SIZE
                lot_filter = next(filter(lambda x: x['filterType'] == 'LOT_SIZE', symbol_info['filters']))
                step_size = float(lot_filter['stepSize'])
                adjusted_quantity = round(base_balance / step_size) * step_size
                
                if adjusted_quantity > 0:
                    client.create_order(
                        symbol=symbol,
                        side='SELL',
                        type='MARKET',
                        quantity=adjusted_quantity
                    )
                    log_to_file(f"Awaryjne zamknięcie pozycji dla {symbol}, ilość: {adjusted_quantity}")
            except Exception as e:
                log_to_file(f"Błąd podczas awaryjnego zamykania pozycji {symbol}: {e}")

        # Zapisz szczegółowy raport błędu
        error_report = {
            "timestamp": int(time.time() * 1000),
            "symbol": symbol,
            "recent_orders": all_orders[-10:],  # ostatnie 10 zleceń
            #"recent_logs": recent_logs,
            "balances": {base_asset: balances.get(base_asset, 0), 
                        quote_asset: balances.get(quote_asset, 0)}
        }
        log_to_file(f"Raport błędu krytycznego dla {symbol}: {error_report}")
        
        # Oznacz sygnał jako zamknięty z błędem
        signal["status"] = "CLOSED"
        signal["error"] = "CRITICAL_ERROR"
        
    except Exception as e:
        log_to_file(f"Błąd podczas obsługi sytuacji krytycznej dla {symbol}: {e}")


def update_stop_loss(signal):
    """
    Aktualizuje poziom stop-loss w zależności od osiągniętych celów.
    """
    try:
        # Pobierz poziomy entry i cele z sygnału
        entry = signal["entry"]
        targets = signal.get("targets", [])
        achieved_targets = signal.get("achieved_targets", [])

        # Ustal nowy poziom stop-loss
        if not achieved_targets:
            # Żaden TP nie jest filled, stop-loss na poziomie pierwotnym
            new_stop_loss = signal["stop_loss"]
        else:
            # Stop-loss na poziomie ostatniego osiągniętego celu
            last_achieved_target_index = max(achieved_targets)
            if last_achieved_target_index == 0:
                new_stop_loss = entry  # Poziom entry
            else:
                new_stop_loss = targets[last_achieved_target_index - 1]  # Ostatni osiągnięty cel

        # Znajdź i anuluj istniejące zlecenie stop-loss
        stop_loss_orders = [o for o in signal.get("orders", []) if o["type"] == "STOP_LOSS" and o["status"] != "FILLED"]
        if stop_loss_orders:
            for order in stop_loss_orders:
                client.cancel_order(symbol=signal["currency"], orderId=order["orderId"])
                log_to_file(f"Anulowano stary stop-loss {order['orderId']} dla {signal['currency']}")

            # Złóż nowe zlecenie stop-loss
            new_order = client.create_order(
                symbol=signal["currency"],
                side="BUY" if signal["signal_type"] == "SHORT" else "SELL",
                type="STOP_LOSS_LIMIT",
                quantity=stop_loss_orders[0]["quantity"],
                stopPrice=new_stop_loss,
                price=new_stop_loss
            )
            log_to_file(f"Zaktualizowano stop-loss dla {signal['currency']} na poziom {new_stop_loss}")
            return new_order

    except Exception as e:
        log_to_file(f"Błąd podczas aktualizacji stop-loss dla {signal['currency']}: {e}")
        return None


def check_and_update_signal_history():
    """
    Sprawdza i aktualizuje historię sygnałów, modyfikując zlecenia stop-loss w miarę osiągania celów.
    """
    history = load_signal_history()
    for signal in history:
        if signal.get("status") != "CLOSED":
            symbol = signal["currency"]
            try:
                # Pobierz wszystkie zlecenia z Binance i zaktualizuj strukturę `orders`
                signal = get_orders_for_signal(signal)
                orders = signal.get("orders", [])

                # Sprawdź czy mamy wszystkie wymagane typy zleceń
                market_orders = [o for o in orders if o['type'] == 'MARKET' and o['status'] == 'FILLED']
                stop_loss_orders = [o for o in orders if o['type'] == 'STOP_LOSS']
                take_profit_orders = [o for o in orders if o['type'] == 'TAKE_PROFIT']

                # Weryfikacja poprawności struktury zleceń
                if len(market_orders) != 1 or not stop_loss_orders or not take_profit_orders:
                    handle_critical_error(signal)
                    continue

                # Sprawdź status zleceń
                stop_loss_filled = any(o['status'] == 'FILLED' for o in stop_loss_orders)
                filled_take_profits = [o for o in take_profit_orders if o['status'] == 'FILLED']
                
                # Aktualizuj osiągnięte cele
                for order in filled_take_profits:
                    update_achieved_targets(signal, order['stopPrice'])

                # Sprawdź scenariusze zamknięcia pozycji
                if len(filled_take_profits) == len(take_profit_orders):
                    # 100% sukcesu
                    signal["status"] = "CLOSED"
                    log_to_file(f"Sygnał {symbol} został zamknięty (100% sukces).")
                    cancel_remaining_orders(signal)
                elif stop_loss_filled:
                    if filled_take_profits:
                        # Częściowy sukces
                        signal["status"] = "CLOSED"
                        log_to_file(f"Sygnał {symbol} został zamknięty (częściowy sukces).")
                    else:
                        # Całkowita porażka
                        signal["status"] = "CLOSED"
                        log_to_file(f"Sygnał {symbol} został zamknięty (stop-loss bez osiągnięcia celów).")
                    cancel_remaining_orders(signal)
                elif filled_take_profits:
                    # Sygnał aktywny z częściową realizacją
                    update_stop_loss(signal)
                    log_to_file(f"Sygnał {symbol} osiągnął częściowy cel, zaktualizowano stop-loss.")

            except Exception as e:
                log_to_file(f"Błąd podczas sprawdzania zleceń dla {symbol}: {e}")
    save_signal_history(history)
