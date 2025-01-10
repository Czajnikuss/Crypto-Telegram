import json
import re
import os
from binance_trading import execute_trade
from datetime import datetime

# Plik do przechowywania historii sygnałów
SIGNAL_HISTORY_FILE = 'signal_history.json'
MAX_HISTORY_SIZE = 50  # Maksymalna liczba sygnałów w historii

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

def is_signal_new(signal, history):
    """
    Sprawdza, czy sygnał jest nowy (nie istnieje w historii).
    """
    for existing_signal in history:
        if (existing_signal["currency"] == signal["currency"] and
            existing_signal["signal_type"] == signal["signal_type"] and
            existing_signal["entry"] == signal["entry"] and
            existing_signal["stop_loss"] == signal["stop_loss"] and
            existing_signal["targets"] == signal["targets"]):
            return False
    return True

def parse_signal_message_algo(message_text):
    """
    Parses the signal message text and extracts relevant information.
    """
    # Extract currency and signal type from the first line
    first_line_match = re.search(r'\*\*([A-Za-z0-9]+)\.?[A-Za-z]*\s([A-Za-z]+)\*\*', message_text)
    if first_line_match:
        currency_with_suffix = first_line_match.group(1)
        signal_type = first_line_match.group(2)
        # Remove any suffix after a dot in the currency name
        currency = currency_with_suffix.split('.')[0]
    else:
        currency = None
        signal_type = None

    # Extract entry value
    entry_match = re.search(r'Entry:\s*([\d.]+)', message_text)
    entry = float(entry_match.group(1)) if entry_match else None

    # Extract target values
    take_profit_matches = re.findall(r'Take profit \d+:\s*([\d.]+)', message_text)
    targets = [float(tp) for tp in take_profit_matches]

    # Extract stop loss value
    stop_loss_match = re.search(r'Stop loss:\s*([\d.]+)', message_text)
    stop_loss = float(stop_loss_match.group(1)) if stop_loss_match else None

    # Set breakeven to entry if not specified
    breakeven = entry

    return {
        "currency": currency,
        "signal_type": signal_type,
        "entry": entry,
        "targets": targets,
        "stop_loss": stop_loss,
        "breakeven": breakeven,
    }

async def process_algo_bot_message(message):
    """
    Przetwarza wiadomość sygnału i uruchamia handel, jeśli sygnał jest nowy.
    """
    signal_data = parse_signal_message_algo(message["text"])
    signal_data["date"] = message["date"]

    # Logowanie uzyskanego sygnału
    log_to_file(f"Received signal: {signal_data}")

    # Załaduj historię sygnałów
    history = load_signal_history()

    # Sprawdź, czy sygnał jest nowy
    if is_signal_new(signal_data, history):
        print(f"New signal found: {signal_data}")
        # Wykonaj transakcję
        execute_trade(signal_data, percentage=20)
        # Dodaj sygnał do historii
        history.append(signal_data)
        # Ogranicz historię do MAX_HISTORY_SIZE
        if len(history) > MAX_HISTORY_SIZE:
            history = history[-MAX_HISTORY_SIZE:]
        # Zapisz historię
        save_signal_history(history)
    else:
        print(f"Signal already exists in history: {signal_data}")

