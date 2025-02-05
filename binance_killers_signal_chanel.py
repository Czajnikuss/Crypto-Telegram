import json
import re, os
from binance_trading import execute_trade
import logging
from datetime import datetime
from telethon.tl.types import Channel
from common import log_to_file, MAX_HISTORY_SIZE, load_signal_history, save_signal_history, is_signal_new, last_message_ids, ask_AI_to_fill_the_signal_fields


async def get_binance_killers_signals_channel(client_telegram):
    async for dialog in client_telegram.iter_dialogs():
        if dialog.name == "Binance Killers®" and isinstance(dialog.entity, Channel):
            return dialog.entity
    return None

async def display_last_messages(client_telegram, limit=20):
    channel = await get_binance_killers_signals_channel(client_telegram)
    if not channel:
        print("Kanał 'Binance Killers®' nie został znaleziony.")
        return

    messages = await client_telegram.get_messages(channel, limit=limit)
    for message in messages:
        print(f"\n{message.text}")
        print("-" * 50)

async def check_binance_killers_signals_messages(client_telegram):
    channel = await get_binance_killers_signals_channel(client_telegram)
    if not channel:
        print("Kanał 'Binance Killers®' nie został znaleziony.")
        return

    messages = await client_telegram.get_messages(channel, limit=3)
    
    for message in messages:
        if message.id not in last_message_ids and "SIGNAL ID:" in message.text:
            log_to_file(f"Nowa wiadomość: {message.text}")
            print(f"Nowy sygnał: {message.text}")
            await process_binance_killers_signal_message({
                "text": message.text,
                "date": message.date.isoformat() if message.date else None
            })
            last_message_ids.add(message.id)

def validate_signal_data(signal_data):
    required_fields = ['currency', 'signal_type', 'entry', 'targets', 'stop_loss']
    for field in required_fields:
        if signal_data.get(field) is None:
            return False, f"Brak wymaganego pola: {field}"

    if signal_data['signal_type'] == 'LONG':
        if signal_data['stop_loss'] >= signal_data['entry']:
            return False, "Stop-loss dla LONG powinien być mniejszy niż entry"
        for target in signal_data['targets']:
            if target <= signal_data['entry']:
                return False, "Target dla LONG powinien być większy niż entry"
    elif signal_data['signal_type'] == 'SHORT':
        if signal_data['stop_loss'] <= signal_data['entry']:
            return False, "Stop-loss dla SHORT powinien być większy niż entry"
        for target in signal_data['targets']:
            if target >= signal_data['entry']:
                return False, "Target dla SHORT powinien być mniejszy niż entry"

    if signal_data['signal_type'] == 'LONG' and signal_data['targets'] != sorted(signal_data['targets']):
        return False, "Targety dla LONG powinny być posortowane rosnąco"
    if signal_data['signal_type'] == 'SHORT' and signal_data['targets'] != sorted(signal_data['targets'], reverse=True):
        return False, "Targety dla SHORT powinny być posortowane malejąco"

    return True, "Sygnał jest poprawny"

def parse_binance_killers_signal_message(message_text):
    try:
        try:
            message_text = message_text.encode('latin1').decode('utf-8')
        except Exception:
            pass

        signal_id_pattern = r"SIGNAL ID: #(\d+)"
        coin_patterns = [
            r"\$([A-Z]+)/USDT",
            r"\*\*COIN: \*\*\*\*\$([A-Z]+)\*\*\*\*/USDT",
            r"#([A-Z]+)USDT \|",
            r"Crypto Signal Alert: #([A-Z]+)USDT"
        ]
        direction_patterns = [
            r"Direction: (LONG|SHORT)",
            r"\| (LONG|SHORT)",
            r"#[A-Z]+ \| (LONG|SHORT)"
        ]
        targets_patterns = [
            r"Target \d+: ([\d.]+)(?:[^\d.]|$)",
            r"Target \d+: ([\d.]+)✅",
            r"Targets?: ([\d., ]+)"
        ]
        entry_patterns = [
            r"Entry Zone: ([\d.]+)",
            r"Entry: ([\d.]+)",
            r"Target 1: ([\d.]+)"
        ]
        stop_loss_patterns = [
            r"Stop-Loss: ([\d.]+)",
            r"Stop Loss: ([\d.]+)",
            r"SL: ([\d.]+)"
        ]
        breakeven_patterns = [
            r"Move to breakeven after hitting ([\d.]+)",
            r"Breakeven: ([\d.]+)"
        ]
        profit_pattern = r"🔥([\d.]+)% Profit"

        signal_id = re.search(signal_id_pattern, message_text)
        
        coin = None
        for pattern in coin_patterns:
            coin = re.search(pattern, message_text)
            if coin:
                break

        direction = None
        for pattern in direction_patterns:
            direction = re.search(pattern, message_text)
            if direction:
                break

        entry = None
        for pattern in entry_patterns:
            entry = re.search(pattern, message_text)
            if entry:
                break

        targets = []
        for pattern in targets_patterns:
            if "Targets?" in pattern:
                target_match = re.search(pattern, message_text)
                if target_match:
                    targets = [float(t.strip()) for t in target_match.group(1).split(",")]
                    break
            else:
                targets_found = re.findall(pattern, message_text)
                if targets_found:
                    targets = [float(t) for t in targets_found]
                    break

        if targets and direction:
            if direction.group(1).upper() == "LONG":
                targets = sorted(targets)
            elif direction.group(1).upper() == "SHORT":
                targets = sorted(targets, reverse=True)

        stop_loss = None
        for pattern in stop_loss_patterns:
            stop_loss = re.search(pattern, message_text)
            if stop_loss:
                break

        breakeven = None
        for pattern in breakeven_patterns:
            breakeven = re.search(pattern, message_text)
            if breakeven:
                break

        profit = re.search(profit_pattern, message_text)

        signal_data = {
            "signal_id": signal_id.group(1) if signal_id else "unknown",
            "currency": f"{coin.group(1)}USDT" if coin else None,
            "signal_type": direction.group(1) if direction else None,
            "entry": float(entry.group(1)) if entry else (targets[0] if targets else None),
            "targets": targets[1:] if len(targets) > 1 else [],
            "stop_loss": float(stop_loss.group(1)) if stop_loss else None,
            "breakeven": float(breakeven.group(1)) if breakeven else None,
            "profit_percentage": float(profit.group(1)) if profit else None
        }

        if signal_data["stop_loss"] is None and signal_data["entry"] is not None:
            if signal_data["signal_type"] == "LONG":
                signal_data["stop_loss"] = signal_data["entry"] * 0.95
            elif signal_data["signal_type"] == "SHORT":
                signal_data["stop_loss"] = signal_data["entry"] * 1.05

        required_keys = ['currency', 'signal_type', 'entry', 'targets', 'stop_loss']
        missing_fields = [key for key in required_keys if signal_data.get(key) is None]
        if missing_fields and len(missing_fields) <= 2:
            signal_data = ask_AI_to_fill_the_signal_fields(message_text, signal_data)

        is_valid, validation_message = validate_signal_data(signal_data)
        if not is_valid:
            print(f"Ostrzeżenie: {validation_message}")
            return None

        return signal_data

    except Exception as e:
        print(f"Błąd parsowania wiadomości: {str(e)}")
        return None

async def process_binance_killers_signal_message(message):
    signal_data = parse_binance_killers_signal_message(message["text"])
    
    if signal_data is not None:
        signal_data["date"] = message.get("date", datetime.now().isoformat())
        
        if all([signal_data["currency"], signal_data["signal_type"], signal_data["entry"]]):
            log_to_file(f"Uzyskany sygnał: {signal_data}")
            history = load_signal_history()

            if is_signal_new(signal_data, history):
                print(f"Nowy sygnał znaleziony: {signal_data}")
                execute_trade(signal_data, percentage=20)
                history.append(signal_data)
                if len(history) > MAX_HISTORY_SIZE:
                    history = history[-MAX_HISTORY_SIZE:]
                save_signal_history(history)
            else:
                print(f"Sygnał już istnieje w historii: {signal_data}")
        else:
            print(f"Niepełne dane sygnału: {signal_data}")
