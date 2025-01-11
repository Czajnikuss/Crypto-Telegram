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
    try:
        if not message_text or not isinstance(message_text, str):
            log_to_file("Invalid message format")
            return None

        log_to_file(f"Rozpoczynam parsowanie wiadomości:\n{message_text}")
        
        # Normalizacja tekstu
        message_text = message_text.replace('ń…', 'x').replace('đź', '').replace('х', 'x').replace('👉', '')
        
        # Wykrywanie negatywnych markerów
        negative_markers = ["All entry targets achieved", "All targets achieved", "targets achieved", "Cancelled"]
        if any(marker in message_text for marker in negative_markers):
            log_to_file("Wiadomość zawiera negatywny marker")
            return None

        # Szukanie waluty - różne możliwe formaty
        currency = None
        currency_patterns = [
            r'#([A-Z0-9]+/USDT)',
            r'#(1000[A-Z0-9]+/USDT)',
        ]
        
        for pattern in currency_patterns:
            match = re.search(pattern, message_text)
            if match:
                currency = match.group(1).replace('/', '')
                log_to_file(f"Znaleziona waluta: {currency}")
                break
                
        if not currency:
            log_to_file("Nie znaleziono waluty")
            return None

        # Szukanie kierunku - różne możliwe formaty
        signal_type = None
        direction_patterns = [
            (r'Signal Type:\s*Regular\s*\(\s*(Long|Short)', 1),
            (r'𝓓𝓲𝓻𝓮𝓬𝓽𝓲𝓸𝓷\s*:\s*(LONG|SHORT)', 2),
            (r'^\s*(LONG|SHORT)\s*$', 1),
            (r'Direction\s*:\s*(LONG|SHORT)', 1),
            (r'Signal Type[^:]*:\s*[^(]*\(\s*(Long|Short)', 1)
        ]
        
        for pattern, group in direction_patterns:
            match = re.search(pattern, message_text, re.IGNORECASE | re.MULTILINE)
            if match:
                signal_type = match.group(group).upper()
                log_to_file(f"Znaleziony kierunek: {signal_type}")
                break

        if not signal_type:
            log_to_file("Nie znaleziono kierunku")
            return None

        # Szukanie entry - różne możliwe formaty
        entry = None
        entry_patterns = [
            r'Entry[^:]*:?\s*([\d.]+)(?:\s*-\s*([\d.]+))?',
            r'Entry Targets:\s*([\d.]+)',
        ]
        
        for pattern in entry_patterns:
            match = re.search(pattern, message_text)
            if match:
                entry_low = float(match.group(1))
                entry_high = float(match.group(2)) if match.group(2) else entry_low
                entry = (entry_low + entry_high) / 2
                decimal_places = len(str(entry_low).split('.')[-1]) if '.' in str(entry_low) else 0
                entry = round(entry, decimal_places)
                log_to_file(f"Znalezione entry: {entry}")
                break

        if not entry:
            log_to_file("Nie znaleziono entry")
            return None

        # Szukanie targetów - analiza linii po linii
        target_patterns = [
            r'(?:\d+[\s)*.-]+)([\d.]+)',
            r'Target\s*\d+\s*[-:]\s*([\d.]+)'
        ]

        targets = []
        lines = message_text.split('\n')
        in_target_section = False

        for line in lines:
            if 'Take-Profit' in line or 'Target' in line:
                in_target_section = True
                continue

            if in_target_section:
                for pattern in target_patterns:
                    target_match = re.search(pattern, line)
                    if target_match:
                        try:
                            target_value = float(target_match.group(1))
                            # Sprawdzenie logiczności targetu względem entry i kierunku
                            if signal_type == "LONG" and target_value > entry:
                                targets.append(target_value)
                                log_to_file(f"Dodano target: {target_value}")
                            elif signal_type == "SHORT" and target_value < entry:
                                targets.append(target_value)
                                log_to_file(f"Dodano target: {target_value}")
                        except ValueError:
                            log_to_file(f"Nieprawidłowa wartość targetu w linii: {line}")
                            continue
                # Koniec sekcji targetów, jeśli linia jest pusta lub nie zawiera liczb
                if line.strip() == "" or not any(c.isdigit() for c in line):
                    in_target_section = False

        if not targets:
            log_to_file("Nie znaleziono prawidłowych targetów")
            return None


        # Sprawdzenie czy pierwszy target jest logiczny (nie za daleko od entry)
        first_target_deviation = abs((targets[0] - entry) / entry) * 100
        if first_target_deviation > 5:  # 5% jako maksymalna różnica dla pierwszego targetu
            log_to_file(f"Pierwszy target zbyt odległy od entry: {first_target_deviation}%")
            return None

        # Szukanie stop loss
        stop_loss = None
        stop_loss_patterns = [
            r'Stop[^:]*:?\s*([\d.]+)(?:\s*-\s*([\d.]+)?%?)',
            r'Stoploss\s*:\s*([\d.]+)',
        ]

        for pattern in stop_loss_patterns:
            match = re.search(pattern, message_text, re.IGNORECASE)
            if match:
                stop_loss_raw = match.group(1)
                if '%' in stop_loss_raw:
                    percentage_match = re.search(r'([\d.]+)(?:\s*-\s*([\d.]+))?%', stop_loss_raw)
                    if percentage_match:
                        low_percent = float(percentage_match.group(1))
                        high_percent = float(percentage_match.group(2)) if percentage_match.group(2) else low_percent
                        avg_percent = (low_percent + high_percent) / 2
                        stop_loss = round(entry * (1 - avg_percent / 100), decimal_places)
                else:
                    stop_loss = float(stop_loss_raw)
                break

        # Końcowa walidacja logiczności sygnału
        if stop_loss:
            if (signal_type == "LONG" and stop_loss >= entry) or \
               (signal_type == "SHORT" and stop_loss <= entry):
                log_to_file("Stop loss nielogiczny względem kierunku")
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
