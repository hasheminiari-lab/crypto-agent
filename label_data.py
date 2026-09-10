"""
Crypto-Agent - Data Labeling Script
برچسب‌گذاری سیگنال‌ها با قیمت واقعی 24 ساعت بعد
"""
import sqlite3
import requests
import time
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "bot.db"
BINANCE_API = "https://api.binance.com/api/v3"


def get_price_at_time(symbol: str, target_time: int) -> float:
    """
    گرفتن قیمت یک کوین در زمان مشخص از Binance
    target_time: timestamp به میلی‌ثانیه
    """
    try:
        # تبدیل به timestamp Binance (میلی‌ثانیه)
        end_time = target_time + (24 * 60 * 60 * 1000)  # 24 ساعت بعد
        
        response = requests.get(
            f"{BINANCE_API}/klines",
            params={
                'symbol': f"{symbol}USDT",
                'interval': '1h',
                'startTime': target_time,
                'endTime': end_time,
                'limit': 24
            },
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if data:
                # قیمت بسته شدن آخرین کندل (24 ساعت بعد)
                last_close = float(data[-1][4])
                return last_close
    except Exception as e:
        print(f"   ⚠️ Error fetching {symbol}: {e}")
    
    return 0.0


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
    except:
        pass
    return 0.0


def label_signal_history():
    """برچسب‌گذاری جدول signal_history"""
    print("\n📋 Labeling signal_history...")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # گرفتن سیگنال‌های برچسب‌گذاری نشده
    cursor.execute("""
        SELECT id, coin, entry_price, entry_time 
        FROM signal_history 
        WHERE result_24h IS NULL
        ORDER BY entry_time DESC
    """)
    
    signals = cursor.fetchall()
    print(f"   Found {len(signals)} unlabeled signals")
    
    labeled = 0
    errors = 0
    
    for signal in signals:
        coin = signal['coin']
        entry_price = signal['entry_price']
        entry_time_str = signal['entry_time']
        
        if entry_price <= 0:
            continue
        
        try:
            # تبدیل entry_time به timestamp
            entry_dt = datetime.fromisoformat(entry_time_str)
            entry_timestamp_ms = int(entry_dt.timestamp() * 1000)
            
            # گرفتن قیمت 24 ساعت بعد
            price_24h = get_price_at_time(coin, entry_timestamp_ms)
            
            if price_24h > 0:
                # محاسبه نتیجه
                result_24h = ((price_24h - entry_price) / entry_price) * 100
                was_pump = 1 if result_24h > 20 else 0
                
                # به‌روزرسانی دیتابیس
                cursor.execute("""
                    UPDATE signal_history 
                    SET result_24h = ?, was_pump = ?
                    WHERE id = ?
                """, (round(result_24h, 2), was_pump, signal['id']))
                
                labeled += 1
                print(f"   ✅ {coin:10s} entry=${entry_price:.4f} → 24h=${price_24h:.4f} ({result_24h:+.2f}%) {'🔥' if was_pump else ''}")
            else:
                # اگر قیمت 24h بعد را پیدا نکردیم، از قیمت فعلی استفاده کن
                current_price = get_current_price(coin)
                if current_price > 0:
                    result_24h = ((current_price - entry_price) / entry_price) * 100
                    was_pump = 1 if result_24h > 20 else 0
                    
                    cursor.execute("""
                        UPDATE signal_history 
                        SET result_24h = ?, was_pump = ?
                        WHERE id = ?
                    """, (round(result_24h, 2), was_pump, signal['id']))
                    
                    labeled += 1
                    print(f"   ⚠️ {coin:10s} (using current price) {result_24h:+.2f}%")
                else:
                    errors += 1
                    print(f"   ❌ {coin:10s} price not found")
            
            # Rate limit
            time.sleep(0.2)
            
        except Exception as e:
            errors += 1
            print(f"   ❌ {coin:10s} error: {e}")
    
    conn.commit()
    conn.close()
    
    print(f"\n   ✅ Labeled: {labeled}, Errors: {errors}")
    return labeled


def label_ml_features():
    """برچسب‌گذاری جدول ml_features"""
    print("\n📊 Labeling ml_features...")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # گرفتن رکوردهای برچسب‌گذاری نشده
    cursor.execute("""
        SELECT id, coin, price, timestamp 
        FROM ml_features 
        WHERE label_24h IS NULL
        ORDER BY timestamp DESC
    """)
    
    features = cursor.fetchall()
    print(f"   Found {len(features)} unlabeled features")
    
    labeled = 0
    errors = 0
    
    for feat in features:
        coin = feat['coin']
        price = feat['price']
        timestamp_str = feat['timestamp']
        
        if price <= 0:
            continue
        
        try:
            # تبدیل timestamp
            ts_dt = datetime.fromisoformat(timestamp_str)
            ts_ms = int(ts_dt.timestamp() * 1000)
            
            # گرفتن قیمت 24 ساعت بعد
            price_24h = get_price_at_time(coin, ts_ms)
            
            if price_24h > 0:
                # محاسبه برچسب
                gain_24h = ((price_24h - price) / price) * 100
                label_24h = 1 if gain_24h > 20 else 0
                
                # به‌روزرسانی
                cursor.execute("""
                    UPDATE ml_features 
                    SET label_24h = ?, max_gain_24h = ?
                    WHERE id = ?
                """, (label_24h, round(gain_24h, 2), feat['id']))
                
                labeled += 1
                
                if labeled % 10 == 0:
                    print(f"   ... {labeled} labeled")
            else:
                errors += 1
            
            time.sleep(0.2)
            
        except Exception as e:
            errors += 1
    
    conn.commit()
    conn.close()
    
    print(f"\n   ✅ Labeled: {labeled}, Errors: {errors}")
    return labeled


def verify_results():
    """بررسی نتایج برچسب‌گذاری"""
    print("\n Verifying results...")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # signal_history
    cursor.execute("SELECT COUNT(*) as total FROM signal_history")
    total_sh = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as labeled FROM signal_history WHERE result_24h IS NOT NULL")
    labeled_sh = cursor.fetchone()['labeled']
    
    cursor.execute("SELECT COUNT(*) as pumps FROM signal_history WHERE was_pump = 1")
    pumps_sh = cursor.fetchone()['pumps']
    
    print(f"\n📋 signal_history:")
    print(f"   Total: {total_sh}, Labeled: {labeled_sh}, Pumps: {pumps_sh}")
    
    # ml_features
    cursor.execute("SELECT COUNT(*) as total FROM ml_features")
    total_ml = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as labeled FROM ml_features WHERE label_24h IS NOT NULL")
    labeled_ml = cursor.fetchone()['labeled']
    
    cursor.execute("SELECT COUNT(*) as pumps FROM ml_features WHERE label_24h = 1")
    pumps_ml = cursor.fetchone()['pumps']
    
    print(f"\n📊 ml_features:")
    print(f"   Total: {total_ml}, Labeled: {labeled_ml}, Pumps: {pumps_ml}")
    
    conn.close()


def main():
    """اجرای اصلی"""
    print("=" * 60)
    print("️  DATA LABELING SCRIPT")
    print("=" * 60)
    
    # برچسب‌گذاری signal_history
    label_signal_history()
    
    # برچسب‌گذاری ml_features
    label_ml_features()
    
    # بررسی نتایج
    verify_results()
    
    print("\n" + "=" * 60)
    print("✅ Labeling complete! You can now run backtest.py")
    print("=" * 60)


if __name__ == "__main__":
    main()