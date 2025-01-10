import json
import re, os
from binance_trading import execute_trade
import logging
from datetime import datetime
from telethon.tl.types import Channel
from common import log_to_file, MAX_HISTORY_SIZE, load_signal_history, save_signal_history, is_signal_new, last_message_ids



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



async def get_crypto_signals_channel(client_telegram):
    async for dialog in client_telegram.iter_dialogs():
        if dialog.name == "Crypto Signals" and isinstance(dialog.entity, Channel):
            return dialog.entity
    return None

async def check_crypto_signals_messages(client_telegram):
    channel = await get_crypto_signals_channel(client_telegram)
    if not channel:
        print("Kanał 'Crypto Signals' nie został znaleziony.")
        return

    messages = await client_telegram.get_messages(channel, limit=3)
    
    for message in messages:
        if message.id not in last_message_ids and "Crypto Signal Alert:" in message.text:
            from common import log_to_file
            log_to_file(f"Nowa wiadomość: {message.text}")
            print(f"Nowy sygnał: {message.text}")
            await process_signal_message({
                "text": message.text,
                "date": message.date.isoformat() if message.date else None
            })
            last_message_ids.add(message.id)
            
        if message.id not in last_message_ids and "Powered by @AlgoBot" in message.text:
            from common import log_to_file
            log_to_file(f"Nowa wiadomość: {message.text}")
            print(f"Nowy sygnał: {message.text}")
            await process_algo_bot_message({
                "text": message.text,
                "date": message.date.isoformat() if message.date else None
            })
            last_message_ids.add(message.id)




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