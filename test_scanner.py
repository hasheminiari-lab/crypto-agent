"""
Crypto-Agent v15 - Unit Tests
تست‌های واحد برای scanner_v15
"""
import pytest
from scanner_v15 import is_valid_symbol, calc_v15_score, fear_greed_to_score


def test_is_valid_symbol():
    """تست اعتبارسنجی نماد"""
    # معتبر
    assert is_valid_symbol("BTC") == True
    assert is_valid_symbol("ETH") == True
    assert is_valid_symbol("SOL") == True
    assert is_valid_symbol("DOGE") == True
    
    # نامعتبر
    assert is_valid_symbol("A") == False  # خیلی کوتاه
    assert is_valid_symbol("BTCETHUSDTBNB") == False  # خیلی طولانی
    assert is_valid_symbol("BTC-ETH") == False  # کاراکتر نامعتبر
    assert is_valid_symbol("来财") == False  # کاراکتر چینی
    assert is_valid_symbol("1BTC") == False  # شروع با عدد
    assert is_valid_symbol("") == False  # خالی


def test_calc_v15_score():
    """تست محاسبه امتیاز"""
    # سیگنال خوب
    score, passes = calc_v15_score(
        z4=0.5, m4=1.2, pattern='Monitor', 
        change=10, rsi=50, ml_prob=0.7, social_score=70
    )
    assert passes == True
    assert score >= 50
    
    # سیگنال بد (Z-Score بالا)
    score, passes = calc_v15_score(
        z4=2.0, m4=1.2, pattern='Monitor',
        change=10, rsi=50, ml_prob=0.7, social_score=70
    )
    assert passes == False
    
    # سیگنال بد (Change خیلی منفی)
    score, passes = calc_v15_score(
        z4=0.5, m4=1.2, pattern='Monitor',
        change=-20, rsi=50, ml_prob=0.7, social_score=70
    )
    assert passes == False
    
    # سیگنال بد (الگوی بد)
    score, passes = calc_v15_score(
        z4=0.5, m4=1.2, pattern='Volume Spike',
        change=10, rsi=50, ml_prob=0.7, social_score=70
    )
    assert passes == False


def test_fear_greed_to_score():
    """تست تبدیل Fear & Greed به Social Score"""
    assert fear_greed_to_score(20) == 90  # Extreme Fear
    assert fear_greed_to_score(35) == 70  # Fear
    assert fear_greed_to_score(50) == 50  # Neutral
    assert fear_greed_to_score(65) == 30  # Greed
    assert fear_greed_to_score(80) == 10  # Extreme Greed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])