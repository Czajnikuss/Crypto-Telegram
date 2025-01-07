import asyncio
from telethon import TelegramClient
from telethon.tl.types import Channel
import os
from dotenv import load_dotenv
from crypto_signals_channel import process_signal_message
from algo_bot_messages_processing import process_algo_bot_message
from datetime import datetime
from signal_history_manager import check_and_update_signal_history

load_dotenv()
api_id = os.getenv('API_ID')
api_hash = os.getenv('API_HASH')
client = TelegramClient('session_name', api_id, api_hash)

last_message_ids = set()

def log_to_file(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("logfile.txt", "a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")

async def check_new_messages():
    global last_message_ids
    async for dialog in client.iter_dialogs():
        if dialog.name == "Crypto Signals" and isinstance(dialog.entity, Channel):
            channel = dialog.entity
            break
    else:
        print("Kanał 'Crypto Signals' nie został znaleziony.")
        return

    messages = await client.get_messages(channel, limit=3)
    
    for message in messages:
        if message.id not in last_message_ids and "Crypto Signal Alert:" in message.text:
            log_to_file(f"Nowa wiadomość: {message.text}")
            print(f"Nowy sygnał: {message.text}")
            await process_signal_message({
                "text": message.text,
                "date": message.date.isoformat() if message.date else None
            })
            last_message_ids.add(message.id)
            
        if message.id not in last_message_ids and "Powered by @AlgoBot" in message.text:
            log_to_file(f"Nowa wiadomość: {message.text}")
            print(f"Nowy sygnał: {message.text}")
            await process_algo_bot_message({
                "text": message.text,
                "date": message.date.isoformat() if message.date else None
            })
            last_message_ids.add(message.id)

async def main():
    while True:
        await check_new_messages()
        check_and_update_signal_history()
        await asyncio.sleep(60)

with client:
    client.loop.run_until_complete(main())
