import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))


import unittest
from signal_history_manager import update_signal_high_price, calculate_oco_levels, find_oco_order, find_executed_oco_order


class TestSignalHistoryManager(unittest.TestCase):

    def test_update_signal_high_price_long_no_previous(self):
        signal = {"signal_type": "LONG"}
        current_price = 105.0
        updated = update_signal_high_price(signal.copy(), current_price)
        self.assertEqual(updated["highest_price"], 105.0)

    def test_update_signal_high_price_long_with_previous(self):
        signal = {"signal_type": "LONG", "highest_price": 110.0}
        current_price = 105.0
        updated = update_signal_high_price(signal.copy(), current_price)
        self.assertEqual(updated["highest_price"], 110.0)
        signal2 = {"signal_type": "LONG", "highest_price": 110.0}
        current_price2 = 115.0
        updated = update_signal_high_price(signal2.copy(), current_price2)
        self.assertEqual(updated["highest_price"], 115.0)

    def test_update_signal_high_price_short_no_previous(self):
        signal = {"signal_type": "SHORT"}
        current_price = 85.0
        updated = update_signal_high_price(signal.copy(), current_price)
        self.assertEqual(updated["highest_price"], 85.0)

    def test_update_signal_high_price_short_with_previous(self):
        signal = {"signal_type": "SHORT", "highest_price": 80.0}
        current_price = 85.0
        updated = update_signal_high_price(signal.copy(), current_price)
        self.assertEqual(updated["highest_price"], 80.0)
        signal2 = {"signal_type": "SHORT", "highest_price": 80.0}
        current_price2 = 75.0
        updated = update_signal_high_price(signal2.copy(), current_price2)
        self.assertEqual(updated["highest_price"], 75.0)

    def test_calculate_oco_levels_long_condition_true(self):
        # For LONG, if default highest_price (entry_price) >= any target, then stop_loss = entry_price
        signal = {"signal_type": "LONG", "targets": [100, 110, 120], "stop_loss": 95}
        entry_price = 105.0
        stop_loss, take_profit = calculate_oco_levels(signal, entry_price)
        self.assertEqual(stop_loss, 105.0)
        self.assertEqual(take_profit, 120)

    def test_calculate_oco_levels_long_condition_false(self):
        # For LONG, if highest_price < all targets, then stop_loss = signal['stop_loss']
        signal = {"signal_type": "LONG", "targets": [110, 120, 130], "stop_loss": 100, "highest_price": 105.0}
        entry_price = 105.0
        stop_loss, take_profit = calculate_oco_levels(signal, entry_price)
        self.assertEqual(stop_loss, 100)
        self.assertEqual(take_profit, 130)

    def test_calculate_oco_levels_short_default(self):
        # For SHORT, when no highest_price is provided, default highest_price=entry_price, so condition becomes true
        signal = {"signal_type": "SHORT", "targets": [90, 80, 70], "stop_loss": 95}
        entry_price = 85.0
        stop_loss, take_profit = calculate_oco_levels(signal, entry_price)
        self.assertEqual(stop_loss, 85.0)
        self.assertEqual(take_profit, 70)

    def test_calculate_oco_levels_short_condition_false(self):
        # For SHORT, if highest_price is high such that condition is false, then stop_loss = signal['stop_loss']
        signal = {"signal_type": "SHORT", "targets": [90, 80, 70], "stop_loss": 95, "highest_price": 100.0}
        entry_price = 85.0
        stop_loss, take_profit = calculate_oco_levels(signal, entry_price)
        self.assertEqual(stop_loss, 95)
        self.assertEqual(take_profit, 70)

    def test_find_oco_order(self):
        orders = [
            {"orderListId": 1, "data": "a"},
            {"orderListId": 2, "data": "b"}
        ]
        result = find_oco_order(orders, 2)
        self.assertEqual(result, orders[1])
        result = find_oco_order(orders, 3)
        self.assertIsNone(result)
        result = find_oco_order(orders, None)
        self.assertIsNone(result)

    def test_find_executed_oco_order(self):
        orders_history = [
            {"orderListId": 1, "status": "NEW"},
            {"orderListId": 2, "status": "FILLED"},
            {"orderListId": 2, "status": "CANCELED"}
        ]
        result = find_executed_oco_order(orders_history, 2)
        self.assertEqual(result, orders_history[1])
        result = find_executed_oco_order(orders_history, 1)
        self.assertIsNone(result)
        result = find_executed_oco_order(orders_history, 3)
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
