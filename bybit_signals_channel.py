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

        # Podstawowe sprawdzenie czy wiadomość wygląda jak sygnał
        required_markers = ['Entry', 'Take-Profit']
        if not all(marker in message_text for marker in required_markers):
            log_to_file("Message missing required markers")
            return None

        # Extract currency (tylko jeśli zaczyna się od # i zawiera /)
        currency_match = re.search(r'#([A-Z0-9]+/USDT)\s', message_text)
        if not currency_match:
            log_to_file("Invalid currency format")
            return None
        currency = currency_match.group(1)

        # Extract direction z większą precyzją
        direction_match = re.search(r'(?:Signal Type:\s*(?:Regular)?\s*\((Long|Short)\)|𝓓𝓲𝓻𝓮𝓬𝓽𝓲𝓸𝓷\s*:\s*(LONG|SHORT))', message_text, re.IGNORECASE)
        if not direction_match:
            log_to_file("Direction not found")
            return None
        signal_type = (direction_match.group(1) or direction_match.group(2)).upper()

        # Extract entry values z walidacją
        entry_match = re.search(r'Entry(?:\s*Targets)?[\s:\-]\s*([\d.]+)(?:\s*-\s*([\d.]+))?', message_text)
        if not entry_match:
            log_to_file("Entry price not found")
            return None

        entry_low = float(entry_match.group(1))
        entry_high = float(entry_match.group(2)) if entry_match.group(2) else entry_low
        entry = (entry_low + entry_high) / 2

        # Extract target values z walidacją względem entry
        take_profit_matches = re.findall(r'(?:Take-Profit\s*Targets?:?\s*[\r\n]+(?:\d+\)?[\s:]*|-)?([\d.]+))', message_text)
        targets = []
        min_target_diff = 0.001  # Minimalna różnica 0.1%
        
        for tp in take_profit_matches:
            try:
                target_value = float(tp)
                target_diff = abs((target_value - entry) / entry)
                
                if target_diff < min_target_diff:
                    continue
                    
                if (signal_type == "LONG" and target_value > entry) or \
                   (signal_type == "SHORT" and target_value < entry):
                    targets.append(target_value)
            except ValueError:
                continue

        if not targets:
            log_to_file("No valid targets found")
            return None

        # Extract stop loss z walidacją
        stop_loss = None
        stop_loss_match = re.search(r'Stop(?:loss|[-\s]loss|\sTargets)\s*:?\s*([\d.]+(?:\s*-\s*[\d.]+)?%?)', message_text, re.IGNORECASE)
        
        if stop_loss_match:
            stop_loss_raw = stop_loss_match.group(1)
            if '%' in stop_loss_raw:
                percentage_match = re.search(r'([\d.]+)(?:\s*-\s*([\d.]+))?%', stop_loss_raw)
                if percentage_match:
                    low_percent = float(percentage_match.group(1))
                    high_percent = float(percentage_match.group(2)) if percentage_match.group(2) else low_percent
                    avg_percent = min((low_percent + high_percent) / 2, 20)  # Max 20% stop loss
                    stop_loss = entry * (1 - avg_percent / 100) if signal_type == "LONG" else entry * (1 + avg_percent / 100)
            else:
                sl_values = re.findall(r'[\d.]+', stop_loss_raw)
                if sl_values:
                    stop_loss = float(sl_values[0])
                    if len(sl_values) > 1:
                        stop_loss = (float(sl_values[0]) + float(sl_values[1])) / 2

        if not stop_loss or \
           (signal_type == "LONG" and stop_loss >= entry) or \
           (signal_type == "SHORT" and stop_loss <= entry):
            log_to_file("Invalid stop loss")
            return None

        return {
            "currency": currency,
            "signal_type": signal_type,
            "entry": entry,
            "targets": targets,
            "stop_loss": stop_loss,
            "breakeven": entry
        }

    except Exception as e:
        log_to_file(f"Error parsing signal: {str(e)}")
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
