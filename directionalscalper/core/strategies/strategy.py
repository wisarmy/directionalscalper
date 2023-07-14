from colorama import Fore
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_DOWN
import time
import ta as ta
import os
import logging
from .logger import Logger
from datetime import datetime, timedelta

logging = Logger(filename="strategy.log", stream=True)

class Strategy:
    def __init__(self, exchange, config, manager):
        self.exchange = exchange
        self.config = config
        self.manager = manager
        self.symbol = config.symbol
        self.printed_trade_quantities = False
        self.last_mfirsi_signal = None

    def get_current_price(self, symbol):
        return self.exchange.get_current_price(symbol)

    def limit_order_bybit_unified(self, symbol, side, amount, price, positionIdx, reduceOnly=False):
        params = {"reduceOnly": reduceOnly}
        #print(f"Symbol: {symbol}, Side: {side}, Amount: {amount}, Price: {price}, Params: {params}")
        order = self.exchange.create_limit_order_bybit_unified(symbol, side, amount, price, positionIdx=positionIdx, params=params)
        return order
    
    def limit_order_bybit(self, symbol, side, amount, price, positionIdx, reduceOnly=False):
        params = {"reduceOnly": reduceOnly}
        #print(f"Symbol: {symbol}, Side: {side}, Amount: {amount}, Price: {price}, Params: {params}")
        order = self.exchange.create_limit_order_bybit(symbol, side, amount, price, positionIdx=positionIdx, params=params)
        return order

    def get_open_take_profit_order_quantity(self, orders, side):
        for order in orders:
            if order['side'].lower() == side.lower() and order['reduce_only']:
                return order['qty'], order['id']
        return None, None

    def get_open_take_profit_order_quantities(self, orders, side):
        take_profit_orders = []
        for order in orders:
            if order['side'].lower() == side.lower() and order['reduce_only']:
                take_profit_orders.append((order['qty'], order['id']))
        return take_profit_orders

    def cancel_take_profit_orders(self, symbol, side):
        self.exchange.cancel_close_bybit(symbol, side)

    def calculate_short_conditions(self, short_pos_price, ma_6_low, short_take_profit, short_pos_qty):
        if short_pos_price is not None:
            should_add_to_short = short_pos_price < ma_6_low
            short_tp_distance_percent = ((short_take_profit - short_pos_price) / short_pos_price) * 100
            short_expected_profit_usdt = short_tp_distance_percent / 100 * short_pos_price * short_pos_qty
            logging.info(f"Short TP price: {short_take_profit}, TP distance in percent: {-short_tp_distance_percent:.2f}%, Expected profit: {-short_expected_profit_usdt:.2f} USDT")
            return should_add_to_short, short_tp_distance_percent, short_expected_profit_usdt
        return None, None, None

    def calculate_long_conditions(self, long_pos_price, ma_6_low, long_take_profit, long_pos_qty):
        if long_pos_price is not None:
            should_add_to_long = long_pos_price > ma_6_low
            long_tp_distance_percent = ((long_take_profit - long_pos_price) / long_pos_price) * 100
            long_expected_profit_usdt = long_tp_distance_percent / 100 * long_pos_price * long_pos_qty
            logging.info(f"Long TP price: {long_take_profit}, TP distance in percent: {long_tp_distance_percent:.2f}%, Expected profit: {long_expected_profit_usdt:.2f} USDT")
            return should_add_to_long, long_tp_distance_percent, long_expected_profit_usdt
        return None, None, None
    
    def short_trade_condition(self, current_ask, ma_3_high):
        if current_ask is None or ma_3_high is None:
            return False
        return current_ask > ma_3_high

    def long_trade_condition(self, current_bid, ma_3_low):
        if current_bid is None or ma_3_low is None:
            return False
        return current_bid < ma_3_low

    def add_short_trade_condition(self, short_pos_price, ma_6_high):
        if short_pos_price is None or ma_6_high is None:
            return False
        return short_pos_price > ma_6_high

    def add_long_trade_condition(self, long_pos_price, ma_6_low):
        if long_pos_price is None or ma_6_low is None:
            return False
        return long_pos_price < ma_6_low

    def get_market_data_with_retry(self, symbol, max_retries=5, retry_delay=5):
        for i in range(max_retries):
            try:
                return self.exchange.get_market_data_bybit(symbol)
            except Exception as e:
                if i < max_retries - 1:
                    print(f"Error occurred while fetching market data: {e}. Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    raise e

    def get_balance_with_retry(self, quote_currency, max_retries=5, retry_delay=5):
        for i in range(max_retries):
            try:
                return self.exchange.get_balance_bybit(quote_currency)
            except Exception as e:
                if i < max_retries - 1:
                    print(f"Error occurred while fetching balance: {e}. Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    raise e

    def calc_max_trade_qty(self, total_equity, best_ask_price, max_leverage, max_retries=5, retry_delay=5):
        wallet_exposure = self.config.wallet_exposure
        for i in range(max_retries):
            try:
                market_data = self.exchange.get_market_data_bybit(self.symbol)
                max_trade_qty = round(
                    (float(total_equity) * wallet_exposure / float(best_ask_price))
                    / (100 / max_leverage),
                    int(float(market_data["min_qty"])),
                )
                return max_trade_qty
            except TypeError as e:
                if total_equity is None:
                    print(f"Error: total_equity is None. Retrying in {retry_delay} seconds...")
                if best_ask_price is None:
                    print(f"Error: best_ask_price is None. Retrying in {retry_delay} seconds...")
            except Exception as e:
                print(f"An unexpected error occurred: {e}. Retrying in {retry_delay} seconds...")
            time.sleep(retry_delay)

        raise Exception("Failed to calculate maximum trade quantity after maximum retries.")

    # def calc_max_trade_qty(self, total_equity, best_ask_price, max_leverage):
    #     wallet_exposure = self.config.wallet_exposure
    #     market_data = self.exchange.get_market_data_bybit(self.symbol)
    #     max_trade_qty = round(
    #         (float(total_equity) * wallet_exposure / float(best_ask_price))
    #         / (100 / max_leverage),
    #         int(float(market_data["min_qty"])),
    #     )
    #     return max_trade_qty

    def check_amount_validity_bybit(self, amount, symbol):
        market_data = self.exchange.get_market_data_bybit(symbol)
        min_qty_bybit = market_data["min_qty"]
        if float(amount) < min_qty_bybit:
            print(f"The amount you entered ({amount}) is less than the minimum required by Bybit for {symbol}: {min_qty_bybit}.")
            return False
        else:
            print(f"The amount you entered ({amount}) is valid for {symbol}")
            return True

    def check_amount_validity_once_bybit(self, amount, symbol):
        if not self.check_amount_validity_bybit:
            market_data = self.exchange.get_market_data_bybit(symbol)
            min_qty_bybit = market_data["min_qty"]
            if float(amount) < min_qty_bybit:
                print(f"The amount you entered ({amount}) is less than the minimum required by Bybit for {symbol}: {min_qty_bybit}.")
                return False
            else:
                print(f"The amount you entered ({amount}) is valid for {symbol}")
                return True

    def print_trade_quantities_once_bybit(self, max_trade_qty):
        if not self.printed_trade_quantities:
            wallet_exposure = self.config.wallet_exposure
            best_ask_price = self.exchange.get_orderbook(self.symbol)['asks'][0][0]
            self.exchange.print_trade_quantities_bybit(max_trade_qty, [0.001, 0.01, 0.1, 1, 2.5, 5], wallet_exposure, best_ask_price)
            self.printed_trade_quantities = True

    def get_1m_moving_averages(self, symbol):
        return self.manager.get_1m_moving_averages(symbol)

    def get_5m_moving_averages(self, symbol):
        return self.manager.get_5m_moving_averages(symbol)

    def get_positions_bybit(self):
        position_data = self.exchange.get_positions_bybit(self.symbol)
        return position_data

    def calculate_next_update_time(self):
        # 5 min interval calc
        now = datetime.now()
        next_update_minute = (now.minute // 5 + 1) * 5
        if next_update_minute == 60:
            next_update_minute = 0
            now += timedelta(hours=1)
        return now.replace(minute=next_update_minute, second=0, microsecond=0)

    def calculate_short_take_profit_spread_bybit(self, short_pos_price, symbol, increase_percentage=0):
        if short_pos_price is None:
            return None

        five_min_data = self.manager.get_5m_moving_averages(symbol)
        price_precision = int(self.exchange.get_price_precision(symbol))

        if five_min_data is not None:
            ma_6_high = Decimal(five_min_data["MA_6_H"])
            ma_6_low = Decimal(five_min_data["MA_6_L"])

            try:
                short_target_price = Decimal(short_pos_price) - (ma_6_high - ma_6_low)
            except InvalidOperation as e:
                print(f"Error: Invalid operation when calculating short_target_price. short_pos_price={short_pos_price}, ma_6_high={ma_6_high}, ma_6_low={ma_6_low}")
                return None

            if increase_percentage is None:
                increase_percentage = 0

            # Apply increase percentage to the calculated short target price
            short_target_price = short_target_price * (Decimal('1') - Decimal(increase_percentage) / Decimal('100'))

            try:
                short_target_price = short_target_price.quantize(
                    Decimal('1e-{}'.format(price_precision)),
                    rounding=ROUND_HALF_UP
                )
            except InvalidOperation as e:
                print(f"Error: Invalid operation when quantizing short_target_price. short_target_price={short_target_price}, price_precision={price_precision}")
                return None

            short_profit_price = short_target_price

            return float(short_profit_price)
        return None

    def calculate_short_take_profit_bybit(self, short_pos_price, symbol):
        if short_pos_price is None:
            return None

        five_min_data = self.manager.get_5m_moving_averages(symbol)
        price_precision = int(self.exchange.get_price_precision(symbol))

        if five_min_data is not None:
            ma_6_high = Decimal(five_min_data["MA_6_H"])
            ma_6_low = Decimal(five_min_data["MA_6_L"])

            try:
                short_target_price = Decimal(short_pos_price) - (ma_6_high - ma_6_low)
            except InvalidOperation as e:
                print(f"Error: Invalid operation when calculating short_target_price. short_pos_price={short_pos_price}, ma_6_high={ma_6_high}, ma_6_low={ma_6_low}")
                return None

            try:
                short_target_price = short_target_price.quantize(
                    Decimal('1e-{}'.format(price_precision)),
                    rounding=ROUND_HALF_UP
                )
            except InvalidOperation as e:
                print(f"Error: Invalid operation when quantizing short_target_price. short_target_price={short_target_price}, price_precision={price_precision}")
                return None

            short_profit_price = short_target_price

            return float(short_profit_price)
        return None

    def calculate_long_take_profit_bybit(self, long_pos_price, symbol):
        if long_pos_price is None:
            return None

        five_min_data = self.manager.get_5m_moving_averages(symbol)
        price_precision = int(self.exchange.get_price_precision(symbol))

        if five_min_data is not None:
            ma_6_high = Decimal(five_min_data["MA_6_H"])
            ma_6_low = Decimal(five_min_data["MA_6_L"])

            try:
                long_target_price = Decimal(long_pos_price) + (ma_6_high - ma_6_low)
            except InvalidOperation as e:
                print(f"Error: Invalid operation when calculating long_target_price. long_pos_price={long_pos_price}, ma_6_high={ma_6_high}, ma_6_low={ma_6_low}")
                return None

            try:
                long_target_price = long_target_price.quantize(
                    Decimal('1e-{}'.format(price_precision)),
                    rounding=ROUND_HALF_UP
                )
            except InvalidOperation as e:
                print(f"Error: Invalid operation when quantizing long_target_price. long_target_price={long_target_price}, price_precision={price_precision}")
                return None

            long_profit_price = long_target_price

            return float(long_profit_price)
        return None

    def calculate_long_take_profit_spread_bybit(self, long_pos_price, symbol, increase_percentage=0):
        if long_pos_price is None:
            return None

        five_min_data = self.manager.get_5m_moving_averages(symbol)
        price_precision = int(self.exchange.get_price_precision(symbol))

        if five_min_data is not None:
            ma_6_high = Decimal(five_min_data["MA_6_H"])
            ma_6_low = Decimal(five_min_data["MA_6_L"])

            try:
                long_target_price = Decimal(long_pos_price) + (ma_6_high - ma_6_low)
            except InvalidOperation as e:
                print(f"Error: Invalid operation when calculating long_target_price. long_pos_price={long_pos_price}, ma_6_high={ma_6_high}, ma_6_low={ma_6_low}")
                return None

            if increase_percentage is None:
                increase_percentage = 0

            # Add the specified percentage to the take profit target price
            long_target_price = long_target_price * (1 + Decimal(increase_percentage)/100)

            try:
                long_target_price = long_target_price.quantize(
                    Decimal('1e-{}'.format(price_precision)),
                    rounding=ROUND_HALF_UP
                )
            except InvalidOperation as e:
                print(f"Error: Invalid operation when quantizing long_target_price. long_target_price={long_target_price}, price_precision={price_precision}")
                return None

            long_profit_price = long_target_price

            return float(long_profit_price)
        return None
    
    def check_short_long_conditions(self, best_bid_price, ma_3_high):
        should_short = best_bid_price > ma_3_high
        should_long = best_bid_price < ma_3_high
        return should_short, should_long

    def get_5m_averages(self):
        ma_values = self.manager.get_5m_moving_averages(self.symbol)
        if ma_values is not None:
            high_value = ma_values["MA_3_H"]
            low_value = ma_values["MA_3_L"]
            range_5m = high_value - low_value
            return high_value, low_value
        else:
            return None, None

    def print_lot_sizes(self, max_trade_qty, market_data):
        print(f"Min Trade Qty: {market_data['min_qty']}")
        self.print_lot_size(1, Fore.LIGHTRED_EX, max_trade_qty, market_data)
        self.print_lot_size(0.01, Fore.LIGHTCYAN_EX, max_trade_qty, market_data)
        self.print_lot_size(0.005, Fore.LIGHTCYAN_EX, max_trade_qty, market_data)
        self.print_lot_size(0.002, Fore.LIGHTGREEN_EX, max_trade_qty, market_data)
        self.print_lot_size(0.001, Fore.LIGHTGREEN_EX, max_trade_qty, market_data)

    def calc_lot_size(self, lot_size, max_trade_qty, market_data):
        trade_qty_x = max_trade_qty / (1.0 / lot_size)
        decimals_count = self.count_decimal_places(market_data['min_qty'])
        trade_qty_x_round = round(trade_qty_x, decimals_count)
        return trade_qty_x, trade_qty_x_round

    def print_lot_size(self, lot_size, color, max_trade_qty, market_data):
        not_enough_equity = Fore.RED + "({:.5g}) Not enough equity"
        trade_qty_x, trade_qty_x_round = self.calc_lot_size(lot_size, max_trade_qty, market_data)
        if trade_qty_x_round == 0:
            trading_not_possible = not_enough_equity.format(trade_qty_x)
            color = Fore.RED
        else:
            trading_not_possible = ""
        print(
            color
            + "{:.4g}x : {:.4g} {}".format(
                lot_size, trade_qty_x_round, trading_not_possible
            )
        )

    def count_decimal_places(self, number):
        decimal_str = str(number)
        if '.' in decimal_str:
            return len(decimal_str.split('.')[1])
        else:
            return 0

    def calculate_trade_quantity(self, symbol, leverage):
        dex_equity = self.exchange.get_balance_bybit('USDT')
        trade_qty = (float(dex_equity) * self.current_wallet_exposure) / leverage
        return trade_qty

    def adjust_position_wallet_exposure(self, symbol):
        if self.current_wallet_exposure > self.wallet_exposure_limit:
            desired_wallet_exposure = self.wallet_exposure_limit
            # Calculate the necessary position size to achieve the desired wallet exposure
            max_trade_qty = self.calculate_trade_quantity(symbol, 1)
            current_trade_qty = self.calculate_trade_quantity(symbol, 1 / self.current_wallet_exposure)
            reduction_qty = current_trade_qty - max_trade_qty
            # Reduce the position to the desired wallet exposure level
            self.exchange.reduce_position_bybit(symbol, reduction_qty)

    def truncate(self, number: float, precision: int) -> float:
        return float(Decimal(number).quantize(Decimal('0.' + '0'*precision), rounding=ROUND_DOWN))

    def format_symbol(self, symbol):
        """
        Format the given symbol string to include a '/' between the base and quote currencies.
        The function handles base currencies of 3 to 4 characters and quote currencies of 3 to 4 characters.
        """
        quote_currencies = ["USDT", "USD", "BTC", "ETH"]
        for quote in quote_currencies:
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return base + '/' + quote
        return None

#### HUOBI ####

    def calculate_short_take_profit_huobi(self, short_pos_price, symbol):
        if short_pos_price is None:
            return None

        five_min_data = self.manager.get_5m_moving_averages(symbol)
        price_precision = int(self.exchange.get_price_precision(symbol))

        if five_min_data is not None:
            ma_6_high = Decimal(five_min_data["MA_6_H"])
            ma_6_low = Decimal(five_min_data["MA_6_L"])

            short_target_price = Decimal(short_pos_price) - (ma_6_high - ma_6_low)
            short_target_price = short_target_price.quantize(
                Decimal('1e-{}'.format(price_precision)),
                #rounding=ROUND_HALF_UP
                rounding=ROUND_DOWN
            )

            short_profit_price = short_target_price

            return float(short_profit_price)
        return None

    def calculate_long_take_profit_huobi(self, long_pos_price, symbol):
        if long_pos_price is None:
            return None

        five_min_data = self.manager.get_5m_moving_averages(symbol)
        price_precision = int(self.exchange.get_price_precision(symbol))

        if five_min_data is not None:
            ma_6_high = Decimal(five_min_data["MA_6_H"])
            ma_6_low = Decimal(five_min_data["MA_6_L"])

            long_target_price = Decimal(long_pos_price) + (ma_6_high - ma_6_low)
            long_target_price = long_target_price.quantize(
                Decimal('1e-{}'.format(price_precision)),
                rounding=ROUND_HALF_UP
            )

            long_profit_price = long_target_price

            return float(long_profit_price)
        return None

    def get_open_take_profit_order_quantities_huobi(self, orders, side):
        take_profit_orders = []
        for order in orders:
            order_info = {
                "id": order['id'],
                "price": order['price'],
                "qty": order['qty'],
                "order_status": order['order_status'],
                "side": order['side']
            }
            if (
                order_info['side'].lower() == side.lower()
                and order_info['order_status'] == '3'  # Adjust the condition based on your order status values
                and order_info['id'] not in (self.long_entry_order_ids if side.lower() == 'sell' else self.short_entry_order_ids)
            ):
                take_profit_orders.append((order_info['qty'], order_info['id']))
        return take_profit_orders


    def get_open_take_profit_order_quantity_huobi(self, symbol, orders, side):
        current_price = self.get_current_price(symbol)  # You'd need to implement this function
        long_quantity = None
        long_order_id = None
        short_quantity = None
        short_order_id = None

        for order in orders:
            order_price = float(order['price'])
            if order['side'] == 'sell':
                if side == "close_long" and order_price > current_price:
                    if 'reduce_only' in order and order['reduce_only']:
                        continue
                    long_quantity = order['qty']
                    long_order_id = order['id']
                elif side == "close_short" and order_price < current_price:
                    if 'reduce_only' in order and order['reduce_only']:
                        continue
                    short_quantity = order['qty']
                    short_order_id = order['id']
            else:
                if side == "close_short" and order_price > current_price:
                    if 'reduce_only' in order and not order['reduce_only']:
                        continue
                    short_quantity = order['qty']
                    short_order_id = order['id']
                elif side == "close_long" and order_price < current_price:
                    if 'reduce_only' in order and not order['reduce_only']:
                        continue
                    long_quantity = order['qty']
                    long_order_id = order['id']

        if side == "close_long":
            return long_quantity, long_order_id
        elif side == "close_short":
            return short_quantity, short_order_id

        return None, None

    def calculate_actual_quantity_huobi(self, position_qty, parsed_symbol_swap):
        contract_size_per_unit = self.exchange.get_contract_size_huobi(parsed_symbol_swap)
        return position_qty * contract_size_per_unit

    def parse_symbol_swap_huobi(self, symbol):
        if "huobi" in self.exchange.name.lower():
            base_currency = symbol[:-4]
            quote_currency = symbol[-4:] 
            return f"{base_currency}/{quote_currency}:{quote_currency}"
        return symbol

    def cancel_take_profit_orders_huobi(self, symbol, side):
        self.exchange.cancel_close_huobi(symbol, side)

# RSIMFI

    def initialize_MFIRSI(self, symbol):
        df = self.exchange.fetch_ohlcv(symbol, timeframe='5m')

        df['mfi'] = ta.volume.money_flow_index(df['high'], df['low'], df['close'], df['volume'], window=14)
        df['rsi'] = ta.momentum.rsi(df['close'], window=14)
        df['ma'] = ta.trend.sma_indicator(df['close'], window=14)
        df['open_less_close'] = (df['open'] < df['close']).astype(int)

        df['buy_condition'] = ((df['mfi'] < 20) & (df['rsi'] < 35) & (df['open_less_close'] == 1)).astype(int)
        df['sell_condition'] = ((df['mfi'] > 80) & (df['rsi'] > 35) & (df['open_less_close'] == 0)).astype(int)

        return df


    def should_long_MFI(self, symbol):
        df = self.initialize_MFIRSI(symbol)
        condition = df.iloc[-1]['buy_condition'] == 1
        if condition:
            self.last_mfirsi_signal = 'long'
        return condition

    def should_short_MFI(self, symbol):
        df = self.initialize_MFIRSI(symbol)
        condition = df.iloc[-1]['sell_condition'] == 1
        if condition:
            self.last_mfirsi_signal = 'short'
        return condition

    # def should_long_MFI(self, symbol):
    #     df = self.initialize_MFIRSI(symbol)
    #     return df.iloc[-1]['buy_condition'] == 1

    # def should_short_MFI(self, symbol):
    #     df = self.initialize_MFIRSI(symbol)
    #     return df.iloc[-1]['sell_condition'] == 1
