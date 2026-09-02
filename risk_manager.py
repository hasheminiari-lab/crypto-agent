import numpy as np
from typing import Optional

class PositionSizer:
    """محاسبه حجم پوزیشن بر اساس ریسک"""
    
    def __init__(self, account_balance: float, risk_per_trade: float = 2.0):
        self.account_balance = account_balance
        self.risk_per_trade = risk_per_trade / 100  # تبدیل به decimal
    
    def calculate_position_size(self, entry_price: float, stop_loss: float, 
                               confidence_score: float = 50) -> float:
        """
        محاسبه حجم پوزیشن بر اساس:
        - ریسک ثابت (2% از حساب)
        - فاصله استاپ لاس
        - ضریب اطمینان (Z-Score)
        """
        risk_amount = self.account_balance * self.risk_per_trade
        risk_per_unit = abs(entry_price - stop_loss)
        
        if risk_per_unit == 0:
            return 0
        
        # حجم پایه
        base_position_size = risk_amount / risk_per_unit
        
        # تعدیل بر اساس اطمینان
        confidence_multiplier = 0.5 + (confidence_score / 100)  # 0.5 تا 1.5
        adjusted_position_size = base_position_size * confidence_multiplier
        
        # محدود کردن به حداکثر 10% از حساب
        max_position_value = self.account_balance * 0.10
        max_position_size = max_position_value / entry_price
        
        return min(adjusted_position_size * entry_price, max_position_value)

class RiskManager:
    """مدیریت ریسک کامل"""
    
    def __init__(self, account_balance: float, risk_per_trade: float = 2.0, 
                 max_positions: int = 5):
        self.account_balance = account_balance
        self.risk_per_trade = risk_per_trade
        self.max_positions = max_positions
        self.position_sizer = PositionSizer(account_balance, risk_per_trade)
    
    def calculate_position_size(self, entry_price: float, stop_loss: float, 
                               z_score: float = 0) -> float:
        """محاسبه حجم پوزیشن"""
        confidence_score = min(100, max(0, z_score * 20 + 50))  # تبدیل Z-Score به امتیاز
        return self.position_sizer.calculate_position_size(entry_price, stop_loss, confidence_score)
    
    def calculate_risk_reward(self, entry: float, stop: float, target: float) -> float:
        """محاسبه نسبت ریسک به ریوارد"""
        risk = abs(entry - stop)
        reward = abs(target - entry)
        return reward / risk if risk > 0 else 0
    
    def should_take_trade(self, score: int, current_positions: int) -> bool:
        """تصمیم‌گیری برای ورود به معامله"""
        if current_positions >= self.max_positions:
            return False
        if score < 60:
            return False
        return True
    
    def kelly_criterion(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """محاسبه Kelly Criterion برای بهینه‌سازی حجم"""
        if avg_loss == 0:
            return 0
        win_loss_ratio = avg_win / avg_loss
        kelly = win_rate - ((1 - win_rate) / win_loss_ratio)
        return max(0, min(kelly * 0.5, 0.25))  # Half Kelly، حداکثر 25%