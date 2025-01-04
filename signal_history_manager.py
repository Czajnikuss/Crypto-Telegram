import json
import os
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
    """
    symbol = signal["currency"]
    try:
        # Pobierz wszystkie zlecenia (otwarte i zamknięte)
        all_orders = client.get_all_orders(symbol=symbol, limit=100)
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

def check_stop_loss_execution(signal):
    """
    Sprawdza, czy zlecenie stop-loss zostało wykonane.
    """
    for order in signal.get("orders", []):
        if order['type'] == 'STOP_LOSS' and order['status'] == 'FILLED':
            return True
    return False

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

                # Sprawdź, które cele zostały osiągnięte
                for order in signal.get("orders", []):
                    if order['type'] == 'TAKE_PROFIT' and order['status'] == 'FILLED':
                        update_achieved_targets(signal, order['stopPrice'])

                # Sprawdź, czy zlecenie stop-loss zostało wykonane
                if check_stop_loss_execution(signal):
                    signal["status"] = "CLOSED"
                    log_to_file(f"Sygnał {symbol} został zamknięty (stop-loss).")
                    # Anuluj pozostałe zlecenia take-profit
                    cancel_remaining_orders(signal)

            except Exception as e:
                log_to_file(f"Błąd podczas sprawdzania zleceń dla {symbol}: {e}")
    save_signal_history(history)