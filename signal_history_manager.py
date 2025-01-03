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

def update_legacy_signal(signal):
    """
    Aktualizuje starszy sygnał (bez struktury `orders`, `status` i `achieved_targets`) do nowego formatu.
    """
    if "orders" not in signal:
        signal["orders"] = []
    if "status" not in signal:
        signal["status"] = "OPEN"
    if "achieved_targets" not in signal:
        signal["achieved_targets"] = []  # Lista osiągniętych celów
    return signal

def get_orders_for_signal(signal):
    """
    Pobiera wszystkie zlecenia (otwarte i zamknięte) dla danego sygnału z Binance i aktualizuje strukturę `orders`.
    """
    symbol = signal["currency"]
    try:
        # Pobierz wszystkie zlecenia (otwarte i zamknięte)
        all_orders = client.get_all_orders(symbol=symbol)
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

def update_stop_loss_if_needed(signal):
    """
    Aktualizuje stop-loss tylko wtedy, gdy jest to konieczne.
    """
    symbol = signal["currency"]
    stop_loss_order = next((order for order in signal["orders"] if order['type'] == 'STOP_LOSS'), None)
    if not stop_loss_order:
        return

    # Określ, na którym celu jesteśmy
    current_target_index = len(signal["achieved_targets"])

    # Określ nowy poziom stop-loss
    if current_target_index == 0:
        new_stop_loss = signal["stop_loss"]  # Oryginalny stop-loss
    elif current_target_index == 1:
        new_stop_loss = signal["entry"]  # Poziom wejścia
    else:
        new_stop_loss = signal["targets"][current_target_index - 2]  # Poprzedni cel

    # Sprawdź, czy stop-loss jest już ustawiony na odpowiednim poziomie
    if (signal["signal_type"] == "LONG" and stop_loss_order['stopPrice'] >= new_stop_loss) or \
       (signal["signal_type"] == "SHORT" and stop_loss_order['stopPrice'] <= new_stop_loss):
        return  # Stop-loss jest już ustawiony prawidłowo

    # Zaktualizuj stop-loss
    try:
        client.cancel_order(symbol=symbol, orderId=stop_loss_order['orderId'])
        new_order = client.create_order(
            symbol=symbol,
            side=stop_loss_order['side'],
            type='STOP_LOSS',
            quantity=stop_loss_order['quantity'],
            stopPrice=new_stop_loss
        )
        log_to_file(f"Zaktualizowano stop-loss dla {symbol} na {new_stop_loss}.")
        # Zaktualizuj zlecenie w historii
        stop_loss_order['stopPrice'] = new_stop_loss
        stop_loss_order['orderId'] = new_order['orderId']
    except Exception as e:
        log_to_file(f"Błąd podczas aktualizacji stop-loss dla {symbol}: {e}")

def check_and_update_signal_history():
    """
    Sprawdza i aktualizuje historię sygnałów, modyfikując zlecenia stop-loss w miarę osiągania celów.
    """
    history = load_signal_history()
    for signal in history:
        # Aktualizuj starsze sygnały do nowego formatu
        signal = update_legacy_signal(signal)

        if signal.get("status") != "CLOSED":
            symbol = signal["currency"]
            try:
                # Pobierz wszystkie zlecenia z Binance i zaktualizuj strukturę `orders`
                signal = get_orders_for_signal(signal)

                # Sprawdź, czy zlecenie stop-loss zostało wykonane
                if check_stop_loss_execution(signal):
                    signal["status"] = "CLOSED"
                    log_to_file(f"Sygnał {symbol} został zamknięty (stop-loss).")
                    # Anuluj pozostałe zlecenia take-profit
                    cancel_remaining_orders(signal)
                else:
                    # Sprawdź, które cele zostały osiągnięte
                    current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
                    for i, target in enumerate(signal["targets"]):
                        if i not in signal["achieved_targets"]:
                            if (signal["signal_type"] == "LONG" and current_price >= target) or \
                               (signal["signal_type"] == "SHORT" and current_price <= target):
                                signal["achieved_targets"].append(i)
                                log_to_file(f"Osiągnięto cel {i + 1} dla {symbol}.")

                    # Zaktualizuj stop-loss tylko raz na cykl
                    update_stop_loss_if_needed(signal)
            except Exception as e:
                log_to_file(f"Błąd podczas sprawdzania zleceń dla {symbol}: {e}")
    save_signal_history(history)