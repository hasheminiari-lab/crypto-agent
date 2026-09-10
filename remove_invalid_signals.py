"""
Crypto-Agent - Remove Invalid Signals
حذف سیگنال‌هایی که result_24h = -100% (داده نامعتبر)
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "bot.db"


def remove_invalid_signals():
    """حذف سیگنال‌های با result_24h <= -99%"""
    print("\n🗑️ Removing invalid signals (result_24h <= -99%)...")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # شمارش سیگنال‌های نامعتبر
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM signal_history
        WHERE result_24h <= -99
    """)
    invalid_count = cursor.fetchone()['count']
    
    print(f"   Found {invalid_count} invalid signals")
    
    if invalid_count > 0:
        # نمایش نمونه
        cursor.execute("""
            SELECT coin, entry_price, result_24h, entry_time
            FROM signal_history
            WHERE result_24h <= -99
            LIMIT 5
        """)
        print("\n   Sample invalid signals:")
        for row in cursor.fetchall():
            print(f"   {row['coin']:10s} ${row['entry_price']:.4f} → {row['result_24h']:.2f}% | {row['entry_time'][:10]}")
        
        # حذف سیگنال‌های نامعتبر
        cursor.execute("""
            DELETE FROM signal_history
            WHERE result_24h <= -99
        """)
        
        conn.commit()
        print(f"\n   ✅ Deleted {invalid_count} invalid signals")
    else:
        print("   ✅ No invalid signals found")
    
    conn.close()


def verify_results():
    """بررسی نتایج"""
    print("\n🔍 Verifying results...")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(CASE WHEN result_24h > 0 THEN 1 END) as wins,
            COUNT(CASE WHEN result_24h < 0 THEN 1 END) as losses,
            AVG(result_24h) as avg_return,
            SUM(CASE WHEN result_24h > 0 THEN result_24h ELSE 0 END) as total_profit,
            SUM(CASE WHEN result_24h < 0 THEN result_24h ELSE 0 END) as total_loss
        FROM signal_history
        WHERE result_24h IS NOT NULL
    """)
    
    stats = cursor.fetchone()
    win_rate = (stats['wins'] / stats['total'] * 100) if stats['total'] > 0 else 0
    profit_factor = abs(stats['total_profit'] / stats['total_loss']) if stats['total_loss'] != 0 else 0
    
    print(f"\n📊 Final Statistics:")
    print(f"   Total Signals: {stats['total']}")
    print(f"   Wins: {stats['wins']}")
    print(f"   Losses: {stats['losses']}")
    print(f"   Win Rate: {win_rate:.1f}%")
    print(f"   Avg Return: {stats['avg_return']:.2f}%")
    print(f"   Total Profit: {stats['total_profit']:.2f}%")
    print(f"   Total Loss: {stats['total_loss']:.2f}%")
    print(f"   Profit Factor: {profit_factor:.2f}")
    
    # نمایش نمونه سیگنال‌های معتبر
    print("\n   Sample valid signals:")
    cursor.execute("""
        SELECT coin, entry_price, result_24h
        FROM signal_history
        WHERE result_24h IS NOT NULL
        ORDER BY result_24h DESC
        LIMIT 5
    """)
    
    for row in cursor.fetchall():
        print(f"   {row['coin']:10s} ${row['entry_price']:.4f} → {row['result_24h']:+.2f}%")
    
    conn.close()


def main():
    print("=" * 60)
    print("️ REMOVE INVALID SIGNALS")
    print("=" * 60)
    
    # حذف سیگنال‌های نامعتبر
    remove_invalid_signals()
    
    # بررسی نتایج
    verify_results()
    
    print("\n" + "=" * 60)
    print("✅ Cleanup complete! Run backtest.py now")
    print("=" * 60)


if __name__ == "__main__":
    main()