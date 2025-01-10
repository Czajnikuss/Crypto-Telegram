import asyncio
from common import create_telegram_client
from crypto_signals_channel import check_crypto_signals_messages
from bybit_signals_channel import check_bybit_signals_messages
from signal_history_manager import check_and_update_signal_history

client_telegram = create_telegram_client('session_name')

async def main():
    
    
    # Następnie uruchom główną pętlę monitorowania
    while True:
        await check_crypto_signals_messages(client_telegram)
        await check_bybit_signals_messages(client_telegram)
        check_and_update_signal_history()
        await asyncio.sleep(30)
        check_and_update_signal_history()
        await asyncio.sleep(30)

with client_telegram:
    client_telegram.loop.run_until_complete(main())
