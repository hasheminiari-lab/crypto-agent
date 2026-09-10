"""
Crypto-Agent v15 - Deep Analysis (نسخه اصلاح‌شده)
تحلیل همبستگی بین features و نتایج واقعی
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "bot.db"


def analyze_correlations():
    """تحلیل همبستگی بین features و result_24h"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("=" * 70)
    print("🔬 DEEP CORRELATION ANALYSIS")
    print("=" * 70)
    
    # گرفتن همه سیگنال‌های معتبر و تبدیل به dict
    cursor.execute("""
        SELECT * FROM signal_history
        WHERE result_24h IS NOT NULL
        ORDER BY entry_time DESC
    """)
    
    # تبدیل sqlite3.Row به dict برای استفاده از .get()
    signals = [dict(row) for row in cursor.fetchall()]
    print(f"\n📊 Analyzing {len(signals)} signals\n")
    
    # تحلیل بر اساس هر feature
    features_to_analyze = [
        ('score', 'امتیاز کلی', 40),
        ('z_score', 'Z-Score', 1.5),
        ('volume_mult', 'Volume Multiplier', 2.0),
        ('rsi', 'RSI', 60),
    ]
    
    print("📈 Correlation Analysis (Feature → Win Rate & Avg Return):\n")
    print(f"{'Feature':25s} {'Low Range':>10s} {'High Range':>10s} {'Low Win%':>8s} {'High Win%':>8s} {'Low Avg':>8s} {'High Avg':>8s}")
    print("-" * 80)
    
    for feature, name, threshold in features_to_analyze:
        low_signals = [s for s in signals if s.get(feature, threshold) < threshold]
        high_signals = [s for s in signals if s.get(feature, threshold) >= threshold]
        
        if low_signals and high_signals:
            low_win = sum(1 for s in low_signals if s.get('result_24h', 0) > 0) / len(low_signals) * 100
            high_win = sum(1 for s in high_signals if s.get('result_24h', 0) > 0) / len(high_signals) * 100
            low_avg = sum(s.get('result_24h', 0) for s in low_signals) / len(low_signals)
            high_avg = sum(s.get('result_24h', 0) for s in high_signals) / len(high_signals)
            
            print(f"{name:25s} {'<'+str(threshold):>10s} {'≥'+str(threshold):>10s} {low_win:>7.1f}% {high_win:>7.1f}% {low_avg:>+7.2f}% {high_avg:>+7.2f}%")
    
    # تحلیل pattern
    print("\n\n🎯 Pattern Performance Analysis:\n")
    cursor.execute("""
        SELECT pattern, 
               COUNT(*) as count,
               SUM(CASE WHEN result_24h > 0 THEN 1 ELSE 0 END) as wins,
               AVG(result_24h) as avg_return
        FROM signal_history
        WHERE result_24h IS NOT NULL
        GROUP BY pattern
        ORDER BY avg_return DESC
    """)
    
    print(f"{'Pattern':20s} {'Count':>6s} {'Win Rate':>9s} {'Avg Return':>10s}")
    print("-" * 50)
    
    for row in cursor.fetchall():
        win_rate = (row['wins'] / row['count'] * 100) if row['count'] > 0 else 0
        print(f"{row['pattern']:20s} {row['count']:>6d} {win_rate:>8.1f}% {row['avg_return']:>+9.2f}%")
    
    conn.close()
    print("\n" + "=" * 70)


def main():
    print("🔬 Starting Deep Analysis...\n")
    analyze_correlations()
    print("\n✅ Analysis complete!")


if __name__ == "__main__":
    main()