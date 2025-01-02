from flask import Flask, request, jsonify
import requests
import os
from telethon import TelegramClient
from telethon.tl.functions.messages import ReadHistoryRequest

app = Flask(__name__)

# Konfiguracja klienta Telegram
api_id = os.getenv('API_ID')
api_hash = os.getenv('API_HASH')
client = TelegramClient('session_name', api_id, api_hash)

# Funkcja do przetwarzania sygnałów
async def process_signals(channel_name, limit=3):
    async for dialog in client.iter_dialogs():
        if dialog.name == channel_name and isinstance(dialog.entity, Channel):
            channel = dialog.entity
            break
    else:
        print(f"Kanał o nazwie '{channel_name}' nie został znaleziony.")
        return []

    signals = []
    async for message in client.iter_messages(channel, search="Crypto Signal Alert:", limit=limit):
        if message.text:
            signals.append(message.text)
            # Oznacz wiadomość jako przeczytaną
            await client(ReadHistoryRequest(peer=channel, max_id=message.id))
    return signals

# Endpoint dla webhooka
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if 'message' in data:
        message_text = data['message'].get('text', '')
        channel_name = data['message']['chat']['title']
        print(f"Otrzymano wiadomość z kanału {channel_name}: {message_text}")

        # Sprawdź, czy wiadomość zawiera sygnał handlowy
        if "Crypto Signal Alert:" in message_text:
            with client:
                signals = client.loop.run_until_complete(process_signals(channel_name))
                for signal in signals:
                    print(f"Przetwarzanie sygnału: {signal}")
                    # Tutaj dodaj kod do ustawienia transakcji
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(port=5000)