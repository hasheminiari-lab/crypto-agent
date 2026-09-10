"""
Crypto-Agent - Fix Prices Script
اصلاح قیمت‌های اشتباه در دیتابیس و برچسب‌گذاری مجدد
"""
import sqlite3
import requests
import time
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "bot.db"
BINANCE_API = "https://api.binance.com/api/v3"


def get_current_price(symbol: str) -> float:
    """گرفتن قیمت فعلی از Binance"""
    try:
        response = requests.get(
            f"{BINANCE_API}/ticker/price",
            params={'symbol': f"{symbol}USDT"},
            timeout=5
        )
        if response.status_code == 200:
            return float(response.json()['price'])
    except Exception as e:
        print(f"   ⚠️ Error fetching {symbol}: {e}")
    return 0.0


def get_price_24h_after(symbol: str, start_timestamp_ms: int) -> float:
    """
    گرفتن قیمت 24 ساعت بعد از یک timestamp خاص
    فقط برای داده‌های کمتر از 30 روز کار می‌کند
    """
    try:
        end_time = start_timestamp_ms + (48 * 60 * 60 * 1000)  # 48 ساعت بعد
        
        response = requests.get(
            f"{BINANCE_API}/klines",
            params={
                'symbol': f"{symbol}USDT",
                'interval': '1h',
                'startTime': start_timestamp_ms,
                'endTime': end_time,
                'limit': 48
            },
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if data and len(data) >= 24:
                # قیمت بسته شدن کندل 24 ساعت بعد (ایندکس 24)
                price_24h = float(data[24][4])
                return price_24h
    except Exception as e:
        pass
    return 0.0


def fix_signal_history():
    """اصلاح قیمت‌ها در signal_history"""
    print("\n📋 Fixing signal_history prices...")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, coin, entry_price, entry_time, market_cap
        FROM signal_history
        ORDER BY entry_time DESC
    """)
    
    signals = cursor.fetchall()
    print(f"   Found {len(signals)} signals to fix")
    
    fixed = 0
    errors = 0
    
    for signal in signals:
        coin = signal['coin']
        db_price = signal['entry_price']
        db_mc = signal['market_cap'] or 0
        
        # بررسی اینکه آیا قیمت اشتباه است (بزرگتر از 100 برابر قیمت واقعی)
        real_price = get_current_price(coin)
        
        if real_price > 0:
            ratio = db_price / real_price if real_price > 0 else 0
            
            if ratio > 100:  # قیمت قطعاً اشتباه است
                # اصلاح entry_price
                cursor.execute("""
                    UPDATE signal_history 
                    SET entry_price = ?
                    WHERE id = ?
                """, (real_price, signal['id']))
                
                # محاسبه timestamp
                try:
                    entry_dt = datetime.fromisoformat(signal['entry_time'])
                    ts_ms = int(entry_dt.timestamp() * 1000)
                    now_ms = int(datetime.now().timestamp() * 1000)
                    days_old = (now_ms - ts_ms) / (1000 * 60 * 60 * 24)
                    
                    if days_old < 30:
                        # می‌توانیم قیمت 24h بعد را بگیریم
                        price_24h = get_price_24h_after(coin, ts_ms)
                        if price_24h > 0:
                            result_24h = ((price_24h - real_price) / real_price) * 100
                            was_pump = 1 if result_24h > 20 else 0
                            cursor.execute("""
                                UPDATE signal_history 
                                SET result_24h = ?, was_pump = ?
                                WHERE id = ?
                            """, (round(result_24h, 2), was_pump, signal['id']))
                            print(f"   ✅ {coin:10s} ${db_price:>12,.2f} → ${real_price:.4f} | 24h: {result_24h:+.2f}% {'🔥' if was_pump else ''}")
                        else:
                            print(f"   ⚠️ {coin:10s} ${db_price:>12,.2f} → ${real_price:.4f} | 24h price not found")
                    else:
                        # داده قدیمی است، فقط قیمت را اصلاح می‌کنیم
                        cursor.execute("""
                            UPDATE signal_history 
                            SET result_24h = NULL, was_pump = NULL
                            WHERE id = ?
                        """, (signal['id'],))
                        print(f"   ⚠️ {coin:10s} ${db_price:>12,.2f} → ${real_price:.4f} | old data, result cleared")
                    
                    fixed += 1
                except Exception as e:
                    errors += 1
                    print(f"   ❌ {coin:10s} error: {e}")
            else:
                # قیمت درست است
                pass
        
        time.sleep(0.2)  # Rate limit
    
    conn.commit()
    conn.close()
    
    print(f"\n   ✅ Fixed: {fixed}, Errors: {errors}")
    return fixed


def fix_ml_features():
    """اصلاح قیمت‌ها در ml_features"""
    print("\n📊 Fixing ml_features prices...")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, coin, price, timestamp
        FROM ml_features
        ORDER BY timestamp DESC
    """)
    
    features = cursor.fetchall()
    print(f"   Found {len(features)} features to check")
    
    fixed = 0
    errors = 0
    
    for feat in features:
        coin = feat['coin']
        db_price = feat['price']
        
        real_price = get_current_price(coin)
        
        if real_price > 0:
            ratio = db_price / real_price if real_price > 0 else 0
            
            if ratio > 100:
                cursor.execute("""
                    UPDATE ml_features 
                    SET price = ?
                    WHERE id = ?
                """, (real_price, feat['id']))
                
                # محاسبه timestamp
                try:
                    ts_dt = datetime.fromisoformat(feat['timestamp'])
                    ts_ms = int(ts_dt.timestamp() * 1000)
                    now_ms = int(datetime.now().timestamp() * 1000)
                    days_old = (now_ms - ts_ms) / (1000 * 60 * 60 * 24)
                    
                    if days_old < 30:
                        price_24h = get_price_24h_after(coin, ts_ms)
                        if price_24h > 0:
                            gain_24h = ((price_24h - real_price) / real_price) * 100
                            label_24h = 1 if gain_24h > 20 else 0
                            cursor.execute("""
                                UPDATE ml_features 
                                SET label_24h = ?, max_gain_24h = ?
                                WHERE id = ?
                            """, (label_24h, round(gain_24h, 2), feat['id']))
                    else:
                        cursor.execute("""
                            UPDATE ml_features 
                            SET label_24h = NULL, max_gain_24h = NULL
                            WHERE id = ?
                        """, (feat['id'],))
                    
                    fixed += 1
                except Exception as e:
                    errors += 1
        
        time.sleep(0.2)
    
    conn.commit()
    conn.close()
    
    print(f"\n   ✅ Fixed: {fixed}, Errors: {errors}")
    return fixed


def verify_fix():
    """بررسی نتایج اصلاح"""
    print("\n🔍 Verifying fixes...")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # signal_history
    cursor.execute("""
        SELECT coin, entry_price, result_24h, was_pump 
        FROM signal_history 
        WHERE result_24h IS NOT NULL
        LIMIT 5
    """)
    
    print("\n📋 signal_history (sample):")
    for row in cursor.fetchall():
        pump_emoji = "🔥" if row['was_pump'] == 1 else ""
        print(f"   {row['coin']:10s} ${row['entry_price']:.4f} → 24h: {row['result_24h']:+.2f}% {pump_emoji}")
    
    # آمار کلی
    cursor.execute("SELECT COUNT(*) as total FROM signal_history")
    total = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as labeled FROM signal_history WHERE result_24h IS NOT NULL")
    labeled = cursor.fetchone()['labeled']
    
    cursor.execute("SELECT COUNT(*) as pumps FROM signal_history WHERE was_pump = 1")
    pumps = cursor.fetchone()['pumps']
    
    print(f"\n   Total: {total}, Labeled: {labeled}, Pumps: {pumps}")
    
    conn.close()


def main():
    """اجرای اصلی"""
    print("=" * 60)
    print("🔧 FIX PRICES SCRIPT")
    print("=" * 60)
    
    # اصلاح signal_history
    fix_signal_history()
    
    # اصلاح ml_features
    fix_ml_features()
    
    # بررسی نتایج
    verify_fix()
    
    print("\n" + "=" * 60)
    print("✅ Price fix complete! You can now run backtest.py")
    print("=" * 60)


if __name__ == "__main__":
    main()