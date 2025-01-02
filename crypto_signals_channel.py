import json
import re, os
from binance_trading import execute_trade
import logging
from datetime import datetime

# Plik do przechowywania historii sygnałów
SIGNAL_HISTORY_FILE = 'signal_history.json'
MAX_HISTORY_SIZE = 50  # Maksymalna liczba sygnałów w historii

def log_to_file(message):
    """
    Zapisuje wiadomość do pliku logfile.txt z timestampem.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("logfile.txt", "a", encoding="utf-8") as log_file:  # Dodaj encoding="utf-8"
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

def parse_signal_message(message_text):
    """
    Parsuje treść wiadomości sygnału i wyodrębnia informacje.
    """
    # Wzorce regex do wyodrębniania danych
    currency_pattern = r"Crypto Signal Alert: #(\w+)"  # Waluta (np. ACEUSDT)
    signal_type_pattern = r"(SHORT|LONG)"  # Typ sygnału (SHORT/LONG)
    entry_pattern = r"Entry Zone: ([\d.]+)"  # Kwota wejścia
    targets_pattern = r"Targets: ([\d., ]+)"  # Cele
    stop_loss_pattern = r"Stop-Loss: ([\d.]+)"  # Stop-Loss
    breakeven_pattern = r"Move to breakeven after hitting ([\d.]+)"  # Breakeven

    # Wyodrębnij dane
    currency = re.search(currency_pattern, message_text)
    signal_type = re.search(signal_type_pattern, message_text)
    entry = re.search(entry_pattern, message_text)
    targets = re.search(targets_pattern, message_text)
    stop_loss = re.search(stop_loss_pattern, message_text)
    breakeven = re.search(breakeven_pattern, message_text)

    # Przygotuj wynik
    return {
        "currency": currency.group(1) if currency else None,
        "signal_type": signal_type.group(1) if signal_type else None,
        "entry": float(entry.group(1)) if entry else None,
        "targets": [float(t.strip()) for t in targets.group(1).split(",")] if targets else [],
        "stop_loss": float(stop_loss.group(1)) if stop_loss else None,
        "breakeven": float(breakeven.group(1)) if breakeven else None,
    }

async def process_signal_message(message):
    """
    Przetwarza wiadomość sygnału i uruchamia handel, jeśli sygnał jest nowy.
    """
    signal_data = parse_signal_message(message["text"])
    signal_data["date"] = message["date"]

    # Logowanie uzyskanego sygnału
    log_to_file(f"Uzyskany sygnał: {signal_data}")

    # Załaduj historię sygnałów
    history = load_signal_history()

    # Sprawdź, czy sygnał jest nowy
    if is_signal_new(signal_data, history):
        print(f"Nowy sygnał znaleziony: {signal_data}")
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
        print(f"Sygnał już istnieje w historii: {signal_data}")