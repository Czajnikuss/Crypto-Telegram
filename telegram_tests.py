import asyncio
from telethon import TelegramClient
from telethon.tl.types import Channel, User
import os
from dotenv import load_dotenv
from datetime import datetime
import json

# Wczytaj zmienne środowiskowe
load_dotenv()

# Konfiguracja klienta Telegram
api_id = os.getenv('API_ID')
api_hash = os.getenv('API_HASH')
client = TelegramClient('session_name', api_id, api_hash)

def log_to_file(message):
    """
    Zapisuje wiadomość do pliku logfile.txt z timestampem.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("logfile.txt", "a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")

async def check_new_messages():
    """
    Sprawdza nowe wiadomości na kanałach, filtruje je i zapisuje do pliku JSON.
    """
    async for dialog in client.iter_dialogs():
        if dialog.name == "Crypto Signals" and isinstance(dialog.entity, Channel):
            channel = dialog.entity
            break
    else:
        print("Kanał 'Crypto Signals' nie został znaleziony.")
        return

    # Pobierz ostatnie 5 wiadomości overall
    messages_overall = await client.get_messages(channel, limit=5)
    overall_messages = []

    for message in messages_overall:
        message_data = {
            "id": message.id,
            "text": message.text,
            "date": message.date.isoformat() if message.date else None,
            "sender_id": message.sender_id,
            "sender_name": ""
        }
        try:
            if message.sender:
                sender = await message.get_sender()
                if isinstance(sender, User):
                    message_data["sender_name"] = sender.first_name
                elif isinstance(sender, Channel):
                    message_data["sender_name"] = sender.title
                else:
                    message_data["sender_name"] = "Unknown"
            else:
                message_data["sender_name"] = "Unknown"
        except Exception as e:
            message_data["sender_name"] = "Unknown"
            log_to_file(f"Error getting sender: {e}")
        overall_messages.append(message_data)

    # Pobierz wiadomości zawierające "Powered by @AlgoBot"
    filtered_messages = []
    async for message in client.iter_messages(channel, search="Powered by @AlgoBot", limit=20):
        message_data = {
            "id": message.id,
            "text": message.text,
            "date": message.date.isoformat() if message.date else None,
            "sender_id": message.sender_id,
            "sender_name": ""
        }
        try:
            if message.sender:
                sender = await message.get_sender()
                if isinstance(sender, User):
                    message_data["sender_name"] = sender.first_name
                elif isinstance(sender, Channel):
                    message_data["sender_name"] = sender.title
                else:
                    message_data["sender_name"] = "Unknown"
            else:
                message_data["sender_name"] = "Unknown"
        except Exception as e:
            message_data["sender_name"] = "Unknown"
            log_to_file(f"Error getting sender: {e}")
        filtered_messages.append(message_data)

    # Sortuj wiadomości według daty (od najnowszych)
    filtered_messages.sort(key=lambda x: x["date"], reverse=True)
    # Pobierz ostatnie 5 wiadomości z każdego listu
    last_5_overall = overall_messages
    last_5_filtered = filtered_messages[:5]

    # Zapisz do pliku JSON
    data = {
        "last_5_overall_messages": last_5_overall,
        "last_5_filtered_messages": last_5_filtered
    }
    with open("last_5_messages.json", "w", encoding="utf-8") as json_file:
        json.dump(data, json_file, indent=4, ensure_ascii=False)

# Uruchomienie klienta i pętli
async def main():
    await client.start()
    await check_new_messages()

asyncio.run(main())