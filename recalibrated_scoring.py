"""
Crypto-Agent v15 - Recalibrated Scoring (نسخه اصلاح‌شده)
معکوس کردن سیستم امتیازدهی بر اساس نتایج بک‌تست
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "bot.db"


def apply_new_scoring():
    """اعمال سیستم امتیازدهی جدید (معکوس)"""
    print("\n🔄 Applying new scoring system...")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # بررسی وجود ستون new_score
    cursor.execute("PRAGMA table_info(signal_history)")
    columns = [row['name'] for row in cursor.fetchall()]
    
    if 'new_score' not in columns:
        cursor.execute("ALTER TABLE signal_history ADD COLUMN new_score REAL")
        conn.commit()
        print("   ✅ Added new_score column")
    
    # گرفتن همه سیگنال‌های معتبر
    cursor.execute("""
        SELECT id, coin, score, pattern, z_score, volume_mult, rsi, result_24h
        FROM signal_history
        WHERE result_24h IS NOT NULL
    """)
    
    signals = cursor.fetchall()
    print(f"   Found {len(signals)} signals to rescore")
    
    updated = 0
    for signal in signals:
        # محاسبه امتیاز جدید (معکوس)
        old_score = signal['score'] if signal['score'] is not None else 50
        new_score = 100 - old_score  # معکوس کردن
        
        cursor.execute("""
            UPDATE signal_history
            SET new_score = ?
            WHERE id = ?
        """, (new_score, signal['id']))
        
        updated += 1
    
    conn.commit()
    conn.close()
    
    print(f"   ✅ Updated {updated} signals with inverted scores")


def verify_new_scoring():
    """بررسی عملکرد سیستم جدید"""
    print("\n Verifying new scoring system...")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            CASE 
                WHEN new_score >= 70 THEN '70+'
                WHEN new_score >= 60 THEN '60-69'
                WHEN new_score >= 50 THEN '50-59'
                WHEN new_score >= 40 THEN '40-49'
                WHEN new_score >= 30 THEN '30-39'
                ELSE '0-29'
            END as score_range,
            COUNT(*) as count,
            SUM(CASE WHEN result_24h > 0 THEN 1 ELSE 0 END) as wins,
            AVG(result_24h) as avg_return
        FROM signal_history
        WHERE result_24h IS NOT NULL AND new_score IS NOT NULL
        GROUP BY score_range
        ORDER BY score_range DESC
    """)
    
    print("\n📊 New Score Range Performance:\n")
    print(f"{'Score Range':>10s} {'Count':>6s} {'Win Rate':>9s} {'Avg Return':>10s}")
    print("-" * 40)
    
    for row in cursor.fetchall():
        win_rate = (row['wins'] / row['count'] * 100) if row['count'] > 0 else 0
        print(f"{row['score_range']:>10s} {row['count']:>6d} {win_rate:>8.1f}% {row['avg_return']:>+9.2f}%")
    
    conn.close()


def main():
    print("=" * 70)
    print("🎯 RECALIBRATED SCORING SYSTEM")
    print("=" * 70)
    
    print("\n📋 New Scoring Rules:")
    print("   1. Score Inversion: HIGH score = GOOD signal (معکوس)")
    print("   2. Pattern Priority: Monitor > Volume Spike")
    print("   3. Z-Score Optimal: <1.5 (not extreme)")
    print("   4. Volume Mult Optimal: <2.0x (not extreme)")
    print("   5. RSI Optimal: <60 (not overbought)")
    
    apply_new_scoring()
    verify_new_scoring()
    
    print("\n" + "=" * 70)
    print("✅ Recalibration complete!")
    print("💡 Next: Run backtest.py to see improved results")
    print("=" * 70)


if __name__ == "__main__":
    main()