from binance.client import Client
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from dotenv import load_dotenv
from datetime import datetime, timedelta
from common import get_oco_order_by_orderListId, get_all_oco_orders_for_symbol, get_all_oco_orders


import os, time, json

# Wczytaj zmienne środowiskowe z pliku .env
load_dotenv()

# Pobierz dane z .env
testmode = os.getenv('TESTMODE', 'false').lower() == 'true'  # Domyślnie false
api_key = os.getenv('BINANCE_API_KEY')
api_secret = os.getenv('BINANCE_API_SECRET')

# Inicjalizacja klienta Binance
if testmode:
    print("Używanie środowiska testowego Binance (Testnet).")
    client = Client(api_key, api_secret, testnet=True)
else:
    print("Używanie środowiska produkcyjnego Binance.")
    client = Client(api_key, api_secret)

def get_all_balances():
    """
    Pobiera salda wszystkich dostępnych walut na koncie.
    """
    try:
        account_info = client.get_account()
        balances = account_info['balances']
        return {balance['asset']: float(balance['free']) for balance in balances if float(balance['free']) > 0}
    except Exception as e:
        print(f"Błąd podczas pobierania sald: {e}")
        return {}
    
    
def place_order(symbol, side, quantity):
    side = SIDE_SELL if side == "SELL" else SIDE_BUY
    
    take_profit_order = client.create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )
    print(take_profit_order['orderId'])
    print(take_profit_order['status'])
    return take_profit_order

  
    
def get_algo_orders_count(symbol):
    """
    Pobiera liczbę aktywnych zleceń algorytmicznych dla danej pary handlowej.
    """
    try:
        open_orders = client.get_open_orders(symbol=symbol)
        algo_orders = [take_profit_order for take_profit_order in open_orders if take_profit_order['type'] in ['STOP_LOSS', 'TAKE_PROFIT']]
        return len(algo_orders)
    except Exception as e:
        print(f"Błąd podczas pobierania aktywnych zleceń: {e}")
        return 0
    
def set_stop_loss_order(symbol, side, quantity, stopPrice=0):
    avg_price = float(client.get_avg_price(symbol=symbol)['price'])
    take_profit_order = client.create_order(
                symbol=symbol,
                side=SIDE_BUY if side == SIDE_SELL else SIDE_SELL,
                type="STOP_LOSS",  # Używamy STOP_LOSS
                quantity=quantity,
                stopPrice=stopPrice if stopPrice!= 0 else adjust_price_precision(symbol, avg_price * 1.05) if side == SIDE_SELL else adjust_price_precision(symbol, avg_price * 0.95)
            )
    print(take_profit_order)
    return take_profit_order

def set_take_profit_order(symbol, side, quantity, stopPrice=0):
    avg_price = float(client.get_avg_price(symbol=symbol)['price'])
    take_profit_order = client.create_order(
                symbol=symbol,
                side=SIDE_BUY if side == SIDE_SELL else SIDE_SELL,
                type="TAKE_PROFIT",  # Używamy STOP_LOSS
                quantity=quantity,
                stopPrice=stopPrice if stopPrice!= 0 else adjust_price_precision(symbol, avg_price * 0.95) if side == SIDE_SELL else adjust_price_precision(symbol, avg_price * 1.05)
            )
    print(take_profit_order)
    return take_profit_order

def adjust_price_precision(symbol, price):
    """
    Dostosowuje precyzję ceny do wymagań symbolu.
    """
    symbol_info = client.get_symbol_info(symbol)
    price_filter = next(filter(lambda f: f['filterType'] == 'PRICE_FILTER', symbol_info['filters']))
    tick_size = float(price_filter['tickSize'])
    
    # Oblicz liczbę miejsc dziesiętnych na podstawie tickSize
    precision = len(str(tick_size).rstrip('0').split('.')[1]) if '.' in str(tick_size) else 0
    return float('{:.{}f}'.format(price, precision))
    
def get_order_all(symbol, orderId):
    params = {
            'symbol': symbol,
            'orderId': orderId
        }
    order = client.get_order(**params)
    return order

def adjust_quantity_precision(symbol, quantity):
    """Dostosowuje precyzję ilości do wymagań symbolu"""
    symbol_info = client.get_symbol_info(symbol)
    lot_filter = next(filter(lambda x: x['filterType'] == 'LOT_SIZE', symbol_info['filters']))
    step_size = float(lot_filter['stepSize'])
    precision = len(str(step_size).rstrip('0').split('.')[1]) if '.' in str(step_size) else 0
    return float('{:.{}f}'.format(quantity, precision))



def reset_account_to_usdt():
    """
    Resetuje konto zamieniając wszystkie aktywa na USDT.
    Zwraca słownik z wynikami operacji dla każdej waluty.
    """
    try:
        # Pobierz wszystkie niezerowe salda
        balances = get_all_balances()
        results = {}
        
        # Pomiń USDT, bo to nasza waluta docelowa
        if 'USDT' in balances:
            del balances['USDT']
            
        for asset, amount in balances.items():
            if amount <= 0:
                continue
                
            try:
                # Sprawdź czy istnieje para tradingowa z USDT
                symbol = f"{asset}USDT"
                
                # Sprawdź czy para istnieje
                symbol_info = client.get_symbol_info(symbol)
                if not symbol_info:
                    print(f"Nie znaleziono pary {symbol}")
                    continue
                
                # Dostosuj ilość do zasad LOT_SIZE

                
                adjusted_quantity = adjust_quantity_precision(symbol, amount)
                
                if adjusted_quantity > 0:
                    # Wykonaj sprzedaż do USDT
                    max_retries = 3
                    retry_delay = 1  # sekundy

                    for attempt in range(max_retries):
                        try:
                            order = place_order(symbol=symbol, side="SELL", quantity=adjusted_quantity)
                            if order['status'] != 'EXPIRED':
                                break
                            time.sleep(retry_delay)
                        except Exception as e:
                            print(f"Próba {attempt + 1} nieudana: {str(e)}")
                    results[asset] = {
                        'status': 'success',
                        'amount_sold': adjusted_quantity,
                        'order_id': order['orderId']
                    }
                    print(f"Sprzedano {adjusted_quantity} {asset} na USDT")
                
            except Exception as e:
                results[asset] = {
                    'status': 'error',
                    'error': str(e)
                }
                print(f"Błąd podczas sprzedaży {asset}: {str(e)}")
                
        return results
        
    except Exception as e:
        print(f"Błąd podczas resetowania konta: {str(e)}")
        return None



def cancel_position(symbol: str, days_back: int = 7):
    try:
        # Anuluj wszystkie aktywne zlecenia
        open_orders = client.get_open_orders(symbol=symbol)
        for order in open_orders:
            client.cancel_order(symbol=symbol, orderId=order['orderId'])
            print(f"Anulowano aktywne zlecenie: {order['orderId']}")
        
        # Pobierz historię zleceń z ostatniego tygodnia
        start_time = int((datetime.now() - timedelta(days=days_back)).timestamp() * 1000)
        orders_history = client.get_all_orders(symbol=symbol, startTime=start_time)
        
        # Znajdź ostatnie zlecenie MARKET (otwierające pozycję)
        market_orders = [o for o in orders_history if o['type'] == 'MARKET' and o['status'] == 'FILLED']
        if not market_orders:
            print(f"Nie znaleziono zleceń MARKET dla {symbol} z ostatnich {days_back} dni")
            return None
        
        last_market_order = market_orders[-1]
        original_side = last_market_order['side']
        reverse_side = 'SELL' if original_side == 'BUY' else 'BUY'
        
        # Sprawdź dostępne środki
        account = client.get_account()
        balances = {asset['asset']: float(asset['free']) for asset in account['balances']}
        
        # Pobierz symbol base i quote
        symbol_info = client.get_symbol_info(symbol)
        base_asset = symbol_info['baseAsset']
        quote_asset = symbol_info['quoteAsset']
        
        # Oblicz dostępną ilość do transakcji
        if reverse_side == 'SELL':
            available_quantity = balances.get(base_asset, 0)
            quantity = min(float(last_market_order['executedQty']), available_quantity)
        else:
            # Dla BUY sprawdź dostępne quote asset i aktualną cenę
            ticker = client.get_symbol_ticker(symbol=symbol)
            current_price = float(ticker['price'])
            available_quote = balances.get(quote_asset, 0)
            max_possible = available_quote / current_price
            quantity = min(float(last_market_order['executedQty']), max_possible)
        
        if quantity <= 0:
            print(f"Niewystarczające środki do wykonania zlecenia odwrotnego")
            return None
            
        # Dostosuj ilość do zasad LOT_SIZE
        lot_filter = next(filter(lambda x: x['filterType'] == 'LOT_SIZE', symbol_info['filters']))
        step_size = float(lot_filter['stepSize'])
        quantity = round(quantity / step_size) * step_size
        
        # Złóż zlecenie odwrotne
        reverse_order = client.create_order(
            symbol=symbol,
            side=reverse_side,
            type='MARKET',
            quantity=quantity
        )
        
        print(f"Złożono zlecenie odwrotne:")
        print(f"Symbol: {symbol}")
        print(f"Strona: {reverse_side}")
        print(f"Ilość: {quantity}")
        print(f"Order ID: {reverse_order['orderId']}")
        
        return reverse_order
        
    except Exception as e:
        print(f"Błąd podczas zamykania pozycji: {str(e)}")
        return None

        
#print(reset_account_to_usdt())
#print(get_all_balances())
#print(client.get_open_orders())
#print(place_order("OMNIUSDT", "SELL", 17828.57000000))
#orderId= take_profit_order['orderId']


#cancel_position("IOTAUSDT")
#cancel_position("CHRUSDT")
#cancel_position("AUCTIONUSDT")
#set_stop_loss_order("OMNIUSDT", SIDE_SELL, 1)

#print(get_order_all("OMNIUSDT", 770511))
#print(client.get_open_oco_orders())
#print(json.dumps(get_all_oco_orders_for_symbol(client,"FETUSDT" )))

#print(json.dumps(get_oco_order_by_orderListId(client, 10269), indent=2))
#print(get_order_all("FETUSDT", 325825))

#print(get_order_all("FETUSDT", 325826))
