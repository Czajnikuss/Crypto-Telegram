from telethon.tl.types import Channel
import re, os
from binance_trading import execute_trade
from datetime import datetime
from common import log_to_file, MAX_HISTORY_SIZE, load_signal_history, save_signal_history, is_signal_new, last_message_ids, create_telegram_client

async def get_bybit_signals_channel(client):
    async for dialog in client.iter_dialogs():
        if dialog.name == "Bybit Crypto Signals (Free)" and isinstance(dialog.entity, Channel):
            return dialog.entity
    return None

async def display_last_messages(client_telegram):
    channel = await get_bybit_signals_channel(client_telegram)
    if not channel:
        log_to_file("Kanał 'Bybit Crypto Signals (Free)' nie został znaleziony.")
        return

    messages = await client_telegram.get_messages(channel, limit=15)
    
    for message in messages:
        if message and hasattr(message, 'text') and message.text:
            log_to_file(f"Wiadomość z {message.date}: {message.text}")

def parse_signal_message_byBit_standard(message_text):
    """
    Parses the signal message text and extracts relevant information with stricter validation.
    """
    try:
        if not message_text or not isinstance(message_text, str):
            log_to_file("Invalid message format")
            return None

        log_to_file(f"Rozpoczynam parsowanie wiadomości:\n{message_text}")

        # Normalizacja tekstu - zamiana problematycznych znaków
        message_text = message_text.replace('ń…', 'x').replace('đź', '').replace('х', 'x').replace('👉', '')
        log_to_file("Znormalizowany tekst wiadomości")

        # Podstawowe sprawdzenie czy wiadomość wygląda jak sygnał
        required_markers = ['Entry', 'Take-Profit']
        if not all(marker in message_text for marker in required_markers):
            log_to_file(f"Brak wymaganych markerów. Znaleziono: {[m for m in required_markers if m in message_text]}")
            return None

        # Extract currency (tylko jeśli zaczyna się od # i zawiera /)
        currency_match = re.search(r'#([A-Z0-9]+/USDT)', message_text)
        if not currency_match:
            log_to_file("Nieprawidłowy format waluty")
            return None
        currency = currency_match.group(1).replace('/', '')
        log_to_file(f"Znaleziona waluta: {currency}")

        # Extract direction z większą precyzją i obsługą różnych formatów
        direction_pattern = r'Signal Type:\s*Regular\s*\(\s*Long'
        if re.search(direction_pattern, message_text, re.IGNORECASE):
            signal_type = "LONG"
            log_to_file(f"Znaleziony kierunek: {signal_type}")
        else:
            log_to_file(f"Nie znaleziono kierunku. Szukany pattern: {direction_pattern}")
            return None

        # Extract entry values z walidacją
        entry_match = re.search(r'Entry[^:]*:?[\s\n]*([\d.]+)(?:\s*-\s*([\d.]+))?', message_text)
        if not entry_match:
            log_to_file("Nie znaleziono ceny wejścia")
            return None

        entry_low = float(entry_match.group(1))
        entry_high = float(entry_match.group(2)) if entry_match.group(2) else entry_low
        entry = (entry_low + entry_high) / 2
        
        # Określenie precyzji na podstawie entry
        decimal_places = len(str(entry_low).split('.')[-1]) if '.' in str(entry_low) else 0
        log_to_file(f"Wykryta precyzja: {decimal_places} miejsc po przecinku")
        
        # Zaokrąglenie entry jeśli był liczony jako średnia
        entry = round(entry, decimal_places)
        log_to_file(f"Znaleziona cena wejścia: {entry}")

        # Extract target values z walidacją względem entry
        take_profit_matches = re.findall(r'\d+\)\s*([\d.]+)', message_text)

        targets = []

        for tp in take_profit_matches:
            try:
                target_value = float(tp)
                if target_value > entry:
                    targets.append(target_value)
                    log_to_file(f"Dodano target: {target_value}")
                else:
                    log_to_file(f"Pominięto target {target_value} - mniejszy niż entry {entry}")
            except ValueError:
                log_to_file(f"Nieprawidłowa wartość targetu: {tp}")
                continue

        if not targets:
            log_to_file("Nie znaleziono prawidłowych targetów")
            return None

        # Extract stop loss z walidacją
        stop_loss = None
        stop_loss_match = re.search(r'Stop[^:]*:?[\s\n]*([\d.]+(?:\s*-\s*[\d.]+)?%?)', message_text)

        if stop_loss_match:
            stop_loss_raw = stop_loss_match.group(1)
            log_to_file(f"Znaleziony stop loss raw: {stop_loss_raw}")
            if '%' in stop_loss_raw:
                percentage_match = re.search(r'([\d.]+)(?:\s*-\s*([\d.]+))?%', stop_loss_raw)
                if percentage_match:
                    low_percent = float(percentage_match.group(1))
                    high_percent = float(percentage_match.group(2)) if percentage_match.group(2) else low_percent
                    avg_percent = (low_percent + high_percent) / 2
                    stop_loss = round(entry * (1 - avg_percent / 100), decimal_places)
                    log_to_file(f"Obliczony stop loss z {avg_percent}%: {stop_loss}")

        if not stop_loss or stop_loss >= entry:
            log_to_file(f"Nieprawidłowy stop loss: {stop_loss}")
            return None

        signal_data = {
            "currency": currency,
            "signal_type": signal_type,
            "entry": entry,
            "targets": targets,
            "stop_loss": stop_loss,
            "breakeven": entry
        }
        log_to_file(f"Utworzono sygnał: {signal_data}")
        return signal_data

    except Exception as e:
        log_to_file(f"Błąd podczas parsowania sygnału: {str(e)}")
        return None


async def process_byBit_standard_message(message):
    """
    Przetwarza wiadomość sygnału i uruchamia handel, jeśli sygnał jest nowy.
    """
    if not message or "text" not in message or "date" not in message:
        log_to_file("Invalid message format in process_byBit_standard_message")
        return

    signal_data = parse_signal_message_byBit_standard(message["text"])
    if not signal_data:
        return

    signal_data["date"] = message["date"]
    log_to_file(f"Received signal: {signal_data}")
    
    history = load_signal_history()
    if is_signal_new(signal_data, history):
        log_to_file(f"Processing new signal for {signal_data['currency']}")
        execute_trade(signal_data, percentage=20)
        history.append(signal_data)
        if len(history) > MAX_HISTORY_SIZE:
            history = history[-MAX_HISTORY_SIZE:]
        save_signal_history(history)
    else:
        log_to_file(f"Signal already exists in history for {signal_data['currency']}")

async def check_bybit_signals_messages(client_telegram):
    channel = await get_bybit_signals_channel(client_telegram)
    if not channel:
        log_to_file("Kanał 'Bybit Crypto Signals (Free)' nie został znaleziony.")
        return

    messages = await client_telegram.get_messages(channel, limit=5)
    
    for message in messages:
        if message and message.id not in last_message_ids and hasattr(message, 'text') and message.text:
            if '#' in message.text and any(marker in message.text for marker in ['Entry', 'Take-Profit']):
                log_to_file(f"Nowa wiadomość z Bybit: {message.text}")
                await process_byBit_standard_message({
                    "text": message.text,
                    "date": message.date.isoformat() if message.date else None
                })
                last_message_ids.add(message.id)
