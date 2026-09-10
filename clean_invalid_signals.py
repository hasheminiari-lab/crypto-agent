"""
Crypto-Agent - Clean Invalid Signals
حذف سیگنال‌هایی که قیمت 24h بعد پیدا نشده (delist شده‌اند)
"""
import sqlite3
import requests
from pathlib import Path

DB_PATH = Path(__file__).parent / "bot.db"
BINANCE_API = "https://api.binance.com/api/v3"


def is_coin_active(symbol: str) -> bool:
    """بررسی اینکه آیا کوین هنوز در Binance فعال است"""
    try:
        response = requests.get(
            f"{BINANCE_API}/ticker/price",
            params={'symbol': f"{symbol}USDT"},
            timeout=5
        )
        if response.status_code == 200:
            price = float(response.json()['price'])
            return price > 0
    except:
        pass
    return False


def clean_signal_history():
    """پاکسازی signal_history"""
    print("\n📋 Cleaning signal_history...")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # گرفتن سیگنال‌هایی که result_24h = -100% دارند
    cursor.execute("""
        SELECT id, coin, entry_price, result_24h
        FROM signal_history
        WHERE result_24h <= -99
    """)
    
    invalid_signals = cursor.fetchall()
    print(f"   Found {len(invalid_signals)} signals with -100% result")
    
    cleaned = 0
    kept = 0
    
    for signal in invalid_signals:
        coin = signal['coin']
        
        # بررسی اینکه آیا کوین هنوز فعال است
        if is_coin_active(coin):
            # کوین فعال است، پس داده معتبر است (واقعاً -100% شده)
            kept += 1
            print(f"   ✅ {coin:10s} ${signal['entry_price']:.4f} → -100% (valid - coin still active)")
        else:
            # کوین delist شده، داده نامعتبر است
            cursor.execute("""
                UPDATE signal_history
                SET result_24h = NULL, was_pump = NULL
                WHERE id = ?
            """, (signal['id'],))
            cleaned += 1
            print(f"   🗑️ {coin:10s} ${signal['entry_price']:.4f} → NULL (delisted)")
    
    conn.commit()
    conn.close()
    
    print(f"\n   ✅ Cleaned: {cleaned}, Kept: {kept}")
    return cleaned


def clean_ml_features():
    """پاکسازی ml_features"""
    print("\n📊 Cleaning ml_features...")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # گرفتن فیچرهایی که max_gain_24h = -100% دارند
    cursor.execute("""
        SELECT id, coin, price, max_gain_24h
        FROM ml_features
        WHERE max_gain_24h <= -99
    """)
    
    invalid_features = cursor.fetchall()
    print(f"   Found {len(invalid_features)} features with -100% gain")
    
    cleaned = 0
    
    for feat in invalid_features:
        coin = feat['coin']
        
        if not is_coin_active(coin):
            cursor.execute("""
                UPDATE ml_features
                SET label_24h = NULL, max_gain_24h = NULL
                WHERE id = ?
            """, (feat['id'],))
            cleaned += 1
    
    conn.commit()
    conn.close()
    
    print(f"\n   ✅ Cleaned: {cleaned}")
    return cleaned


def verify_cleanup():
    """بررسی نتایج پاکسازی"""
    print("\n🔍 Verifying cleanup...")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # signal_history
    cursor.execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(CASE WHEN result_24h IS NOT NULL THEN 1 END) as valid,
            COUNT(CASE WHEN result_24h > 0 THEN 1 END) as wins,
            COUNT(CASE WHEN was_pump = 1 THEN 1 END) as pumps,
            AVG(CASE WHEN result_24h IS NOT NULL THEN result_24h END) as avg_return
        FROM signal_history
    """)
    
    stats = cursor.fetchone()
    win_rate = (stats['wins'] / stats['valid'] * 100) if stats['valid'] > 0 else 0
    
    print(f"\n📋 signal_history:")
    print(f"   Total: {stats['total']}")
    print(f"   Valid: {stats['valid']}")
    print(f"   Wins: {stats['wins']}")
    print(f"   Pumps: {stats['pumps']}")
    print(f"   Win Rate: {win_rate:.1f}%")
    print(f"   Avg Return: {stats['avg_return']:.2f}%")
    
    # نمایش نمونه‌های معتبر
    print("\n   Sample valid signals:")
    cursor.execute("""
        SELECT coin, entry_price, result_24h, was_pump
        FROM signal_history
        WHERE result_24h IS NOT NULL
        ORDER BY result_24h DESC
        LIMIT 5
    """)
    
    for row in cursor.fetchall():
        pump = "" if row['was_pump'] == 1 else ""
        print(f"   {row['coin']:10s} ${row['entry_price']:.4f} → {row['result_24h']:+.2f}% {pump}")
    
    conn.close()


def main():
    """اجرای اصلی"""
    print("=" * 60)
    print(" CLEAN INVALID SIGNALS")
    print("=" * 60)
    
    # پاکسازی signal_history
    clean_signal_history()
    
    # پاکسازی ml_features
    clean_ml_features()
    
    # بررسی نتایج
    verify_cleanup()
    
    print("\n" + "=" * 60)
    print("✅ Cleanup complete! You can now run backtest.py")
    print("=" * 60)


if __name__ == "__main__":
    main()