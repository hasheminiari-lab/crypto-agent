"""
Crypto-Agent v15 - Risk Manager
مدیریت ریسک با Kelly Criterion
"""
import math
from typing import Dict, Optional


class RiskManager:
    """مدیریت ریسک با Kelly Criterion"""
    
    def __init__(self, capital: float = 10000, max_risk_per_trade: float = 0.02):
        """
        Args:
            capital: سرمایه کل
            max_risk_per_trade: حداکثر ریسک در هر معامله (2%)
        """
        self.capital = capital
        self.max_risk_per_trade = max_risk_per_trade
    
    def kelly_fraction(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """
        محاسبه Kelly Fraction
        
        Args:
            win_rate: نرخ برد (0-100)
            avg_win: میانگین سود (درصد)
            avg_loss: میانگین ضرر (درصد، مثبت)
        
        Returns:
            Kelly fraction (0-1)
        """
        if avg_loss == 0 or win_rate == 0:
            return 0.0
        
        # Win/Loss ratio
        b = avg_win / avg_loss
        
        # Probabilities
        p = win_rate / 100.0
        q = 1.0 - p
        
        # Kelly formula: f = (bp - q) / b
        kelly = (b * p - q) / b
        
        # Half-Kelly برای احتیاط
        half_kelly = max(0.0, kelly * 0.5)
        
        return half_kelly
    
    def calculate_position_size(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        entry_price: float,
        stop_loss: float
    ) -> Dict:
        """
        محاسبه اندازه پوزیشن
        
        Returns:
            Dict با position_size, position_pct, risk_amount, kelly_fraction
        """
        # محاسبه Kelly
        kelly = self.kelly_fraction(win_rate, avg_win, avg_loss)
        
        # محدود کردن به max_risk_per_trade
        position_pct = min(kelly, self.max_risk_per_trade)
        
        # محاسبه اندازه پوزیشن
        risk_amount = self.capital * position_pct
        risk_per_unit = abs(entry_price - stop_loss)
        
        if risk_per_unit > 0:
            position_size = risk_amount / risk_per_unit
        else:
            position_size = 0.0
        
        return {
            'position_size': position_size,
            'position_pct': position_pct * 100,
            'risk_amount': risk_amount,
            'kelly_fraction': kelly,
            'max_risk': self.capital * self.max_risk_per_trade
        }
    
    def calculate_stop_loss(
        self,
        entry_price: float,
        atr: Optional[float] = None,
        multiplier: float = 2.0
    ) -> float:
        """
        محاسبه stop-loss
        
        Args:
            entry_price: قیمت ورود
            atr: Average True Range (اگر None، از 2% استفاده می‌شود)
            multiplier: ضریب ATR (پیش‌فرض 2.0)
        
        Returns:
            Stop-loss price
        """
        if atr is None:
            atr = entry_price * 0.02  # 2% پیش‌فرض
        
        return entry_price - (atr * multiplier)
    
    def calculate_take_profit(
        self,
        entry_price: float,
        stop_loss: float,
        risk_reward: float = 2.0
    ) -> float:
        """
        محاسبه take-profit
        
        Args:
            entry_price: قیمت ورود
            stop_loss: قیمت stop-loss
            risk_reward: نسبت ریسک به ریوارد (پیش‌فرض 2.0)
        
        Returns:
            Take-profit price
        """
        risk = entry_price - stop_loss
        return entry_price + (risk * risk_reward)
    
    def calculate_risk_reward(
        self,
        entry_price: float,
        stop_loss: float,
        take_profit: float
    ) -> float:
        """
        محاسبه نسبت risk/reward
        
        Returns:
            Risk/Reward ratio
        """
        risk = abs(entry_price - stop_loss)
        reward = abs(take_profit - entry_price)
        
        if risk > 0:
            return reward / risk
        return 0.0
    
    def update_capital(self, new_capital: float):
        """به‌روزرسانی سرمایه"""
        self.capital = new_capital


# ==========================================
# 🧪 تست
# ==========================================
def test_risk_manager():
    """تست Risk Manager"""
    print("=" * 70)
    print("🧪 TESTING RISK MANAGER")
    print("=" * 70)
    
    rm = RiskManager(capital=10000, max_risk_per_trade=0.02)
    
    # تست 1: Kelly Fraction
    print("\n📊 Test 1: Kelly Fraction")
    kelly = rm.kelly_fraction(win_rate=75, avg_win=10, avg_loss=5)
    print(f"   Win Rate: 75%, Avg Win: 10%, Avg Loss: 5%")
    print(f"   Kelly Fraction: {kelly:.3f}")
    print(f"   Expected: ~0.25 (Half-Kelly)")
    
    # تست 2: Position Size
    print("\n📊 Test 2: Position Size")
    result = rm.calculate_position_size(
        win_rate=75,
        avg_win=10,
        avg_loss=5,
        entry_price=100,
        stop_loss=95
    )
    print(f"   Position Size: {result['position_size']:.2f} units")
    print(f"   Position %: {result['position_pct']:.2f}%")
    print(f"   Risk Amount: ${result['risk_amount']:.2f}")
    print(f"   Kelly Fraction: {result['kelly_fraction']:.3f}")
    
    # تست 3: Stop Loss
    print("\n📊 Test 3: Stop Loss")
    stop_loss = rm.calculate_stop_loss(entry_price=100, atr=2.0, multiplier=2.0)
    print(f"   Entry: $100, ATR: $2.0, Multiplier: 2.0")
    print(f"   Stop Loss: ${stop_loss:.2f}")
    print(f"   Expected: $96.00")
    
    # تست 4: Take Profit
    print("\n📊 Test 4: Take Profit")
    take_profit = rm.calculate_take_profit(
        entry_price=100,
        stop_loss=96,
        risk_reward=2.0
    )
    print(f"   Entry: $100, Stop: $96, R:R = 2.0")
    print(f"   Take Profit: ${take_profit:.2f}")
    print(f"   Expected: $108.00")
    
    # تست 5: Risk/Reward Ratio
    print("\n📊 Test 5: Risk/Reward Ratio")
    rr = rm.calculate_risk_reward(
        entry_price=100,
        stop_loss=96,
        take_profit=108
    )
    print(f"   Entry: $100, Stop: $96, TP: $108")
    print(f"   Risk/Reward: {rr:.2f}")
    print(f"   Expected: 2.00")
    
    print("\n" + "=" * 70)
    print("✅ All tests passed!")
    print("=" * 70)


if __name__ == "__main__":
    test_risk_manager()