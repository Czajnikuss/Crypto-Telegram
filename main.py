import asyncio
from telethon import TelegramClient
from telethon.tl.types import Channel
import os
from dotenv import load_dotenv
from crypto_signals_channel import process_signal_message
from algo_bot_messages_processing import process_algo_bot_message
from datetime import datetime
from signal_history_manager import check_and_update_signal_history

# Wczytaj zmienne środowiskowe
load_dotenv()

# Konfiguracja klienta Telegram
api_id = os.getenv('API_ID')
api_hash = os.getenv('API_HASH')
client = TelegramClient('session_name', api_id, api_hash)

# Przechowuj znaczniki czasowe ostatnich wiadomości
last_message_ids = set()

def log_to_file(message):
    """
    Zapisuje wiadomość do pliku logfile.txt z timestampem.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("logfile.txt", "a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")

async def check_new_messages():
    """
    Sprawdza nowe wiadomości na kanałach i przekazuje je do przetwarzania.
    """
    global last_message_ids

    async for dialog in client.iter_dialogs():
        if dialog.name == "Crypto Signals" and isinstance(dialog.entity, Channel):
            channel = dialog.entity
            break
    else:
        print("Kanał 'Crypto Signals' nie został znaleziony.")
        return

    # Pobierz ostatnie wiadomości
    messages = await client.get_messages(channel, limit=3)
    for message in messages:       
        if message.id not in last_message_ids and "Crypto Signal Alert:" in message.text:
            log_to_file(f"Nowa wiadomość: {message.text}")  # Logowanie treści wiadomości
            print(f"Nowy sygnał: {message.text}")
            # Przekaż wiadomość do przetworzenia
            await process_signal_message({
                "text": message.text,
                "date": message.date.isoformat() if message.date else None
            })
            # Dodaj ID wiadomości do zbioru
            last_message_ids.add(message.id)
        if message.id not in last_message_ids and "Powered by @AlgoBot" in message.text:
            log_to_file(f"Nowa wiadomość: {message.text}")  # Logowanie treści wiadomości
            print(f"Nowy sygnał: {message.text}")
            # Przekaż wiadomość do przetworzenia
            await process_algo_bot_message({
                "text": message.text,
                "date": message.date.isoformat() if message.date else None
            })
            # Dodaj ID wiadomości do zbioru
            last_message_ids.add(message.id)

async def main():
    """
    Główna pętla, która sprawdza nowe wiadomości co minutę.
    """
    while True:
        await check_new_messages()
        check_and_update_signal_history()  # Sprawdź i zaktualizuj historię sygnałów
        await asyncio.sleep(60)  # Czekaj 60 sekund przed kolejnym sprawdzeniem
        
with client:
    client.loop.run_until_complete(main())