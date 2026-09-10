"""
Crypto-Agent v15 - Backtest Engine (نسخه نهایی)
بررسی عملکرد سیگنال‌ها با سیستم امتیازدهی جدید
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "bot.db"


def safe_get(d, key, default=0):
    """دریافت امن مقدار با handling None"""
    val = d.get(key, default)
    return val if val is not None else default


def load_signals():
    """بارگذاری سیگنال‌ها از signal_history"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM signal_history 
        WHERE result_24h IS NOT NULL
        ORDER BY entry_time DESC
    """)
    
    signals = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return signals


def load_ml_features():
    """بارگذاری ویژگی‌های ML"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM ml_features 
        WHERE label_24h IS NOT NULL
        ORDER BY timestamp DESC
    """)
    
    features = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return features


def calculate_metrics(signals):
    """محاسبه معیارهای عملکرد"""
    if not signals:
        return {'error': 'No signals with results'}
    
    total = len(signals)
    wins_24h = sum(1 for s in signals if safe_get(s, 'result_24h') > 0)
    wins_48h = sum(1 for s in signals if safe_get(s, 'result_48h') > 0)
    pumps = sum(1 for s in signals if safe_get(s, 'was_pump') == 1)
    
    # محاسبه سود/زیان
    returns_24h = [safe_get(s, 'result_24h') for s in signals]
    returns_48h = [safe_get(s, 'result_48h') for s in signals]
    
    total_return_24h = sum(returns_24h)
    total_return_48h = sum(returns_48h)
    avg_return_24h = total_return_24h / total if total > 0 else 0
    avg_return_48h = total_return_48h / total if total > 0 else 0
    
    win_rate_24h = (wins_24h / total) * 100
    win_rate_48h = (wins_48h / total) * 100
    
    # Profit Factor
    gross_profit_24h = sum(r for r in returns_24h if r > 0)
    gross_loss_24h = abs(sum(r for r in returns_24h if r < 0))
    profit_factor = gross_profit_24h / gross_loss_24h if gross_loss_24h > 0 else 0
    
    # Sharpe Ratio (ساده‌شده)
    avg_ret = sum(returns_24h) / len(returns_24h) if returns_24h else 0
    std_ret = (sum((r - avg_ret) ** 2 for r in returns_24h) / len(returns_24h)) ** 0.5
    sharpe_ratio = (avg_ret / std_ret) * (252 ** 0.5) if std_ret > 0 else 0
    
    # Max Drawdown (ساده‌شده)
    cumulative = 0
    peak = 0
    max_drawdown = 0
    for r in returns_24h:
        cumulative += r
        if cumulative > peak:
            peak = cumulative
        drawdown = peak - cumulative
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    
    return {
        'total_signals': total,
        'wins_24h': wins_24h,
        'wins_48h': wins_48h,
        'pumps_detected': pumps,
        'win_rate_24h': win_rate_24h,
        'win_rate_48h': win_rate_48h,
        'avg_return_24h': avg_return_24h,
        'avg_return_48h': avg_return_48h,
        'total_return_24h': total_return_24h,
        'total_return_48h': total_return_48h,
        'profit_factor': profit_factor,
        'sharpe_ratio': sharpe_ratio,
        'max_drawdown': max_drawdown,
        'gross_profit_24h': gross_profit_24h,
        'gross_loss_24h': gross_loss_24h,
    }


def analyze_by_pattern(signals):
    """تحلیل بر اساس الگو"""
    pattern_stats = {}
    
    for s in signals:
        pattern = s.get('pattern', 'unknown')
        if pattern not in pattern_stats:
            pattern_stats[pattern] = {'count': 0, 'wins': 0, 'total_return': 0}
        
        pattern_stats[pattern]['count'] += 1
        result = safe_get(s, 'result_24h')
        if result > 0:
            pattern_stats[pattern]['wins'] += 1
        pattern_stats[pattern]['total_return'] += result
    
    for pattern, stats in pattern_stats.items():
        stats['win_rate'] = (stats['wins'] / stats['count']) * 100 if stats['count'] > 0 else 0
        stats['avg_return'] = stats['total_return'] / stats['count'] if stats['count'] > 0 else 0
    
    return pattern_stats


def analyze_by_score_range(signals):
    """
    تحلیل بر اساس محدوده امتیاز
    استفاده از new_score اگر موجود باشد، وگرنه score قدیم
    """
    ranges = {
        '90-100': [], '80-89': [], '70-79': [], '60-69': [],
        '50-59': [], '40-49': [], '30-39': [], '0-29': [],
    }
    
    for s in signals:
        # استفاده از new_score اگر موجود باشد، وگرنه score قدیم
        score = s.get('new_score')
        if score is None:
            score = s.get('score', 0)
        
        if score >= 90:
            ranges['90-100'].append(s)
        elif score >= 80:
            ranges['80-89'].append(s)
        elif score >= 70:
            ranges['70-79'].append(s)
        elif score >= 60:
            ranges['60-69'].append(s)
        elif score >= 50:
            ranges['50-59'].append(s)
        elif score >= 40:
            ranges['40-49'].append(s)
        elif score >= 30:
            ranges['30-39'].append(s)
        else:
            ranges['0-29'].append(s)
    
    range_stats = {}
    for range_name, signals_in_range in ranges.items():
        if signals_in_range:
            wins = sum(1 for s in signals_in_range if safe_get(s, 'result_24h') > 0)
            avg_return = sum(safe_get(s, 'result_24h') for s in signals_in_range) / len(signals_in_range)
            range_stats[range_name] = {
                'count': len(signals_in_range),
                'win_rate': (wins / len(signals_in_range)) * 100,
                'avg_return': avg_return,
            }
    
    return range_stats


def analyze_by_z_score(signals):
    """تحلیل بر اساس Z-Score"""
    ranges = {
        '0.0-0.5': [], '0.5-1.0': [], '1.0-1.5': [],
        '1.5-2.0': [], '2.0-3.0': [], '3.0+': [],
    }
    
    for s in signals:
        z = safe_get(s, 'z_score') or safe_get(s, 'z_4h', 1.0)
        
        if z < 0.5:
            ranges['0.0-0.5'].append(s)
        elif z < 1.0:
            ranges['0.5-1.0'].append(s)
        elif z < 1.5:
            ranges['1.0-1.5'].append(s)
        elif z < 2.0:
            ranges['1.5-2.0'].append(s)
        elif z < 3.0:
            ranges['2.0-3.0'].append(s)
        else:
            ranges['3.0+'].append(s)
    
    z_stats = {}
    for range_name, signals_in_range in ranges.items():
        if signals_in_range:
            wins = sum(1 for s in signals_in_range if safe_get(s, 'result_24h') > 0)
            avg_return = sum(safe_get(s, 'result_24h') for s in signals_in_range) / len(signals_in_range)
            z_stats[range_name] = {
                'count': len(signals_in_range),
                'win_rate': (wins / len(signals_in_range)) * 100,
                'avg_return': avg_return,
            }
    
    return z_stats


def analyze_by_volume_mult(signals):
    """تحلیل بر اساس Volume Multiplier"""
    ranges = {
        '1.0-1.5': [], '1.5-2.0': [], '2.0-3.0': [],
        '3.0-5.0': [], '5.0+': [],
    }
    
    for s in signals:
        vol = safe_get(s, 'volume_mult') or safe_get(s, 'vol_mult_4h', 1.5)
        
        if vol < 1.5:
            ranges['1.0-1.5'].append(s)
        elif vol < 2.0:
            ranges['1.5-2.0'].append(s)
        elif vol < 3.0:
            ranges['2.0-3.0'].append(s)
        elif vol < 5.0:
            ranges['3.0-5.0'].append(s)
        else:
            ranges['5.0+'].append(s)
    
    vol_stats = {}
    for range_name, signals_in_range in ranges.items():
        if signals_in_range:
            wins = sum(1 for s in signals_in_range if safe_get(s, 'result_24h') > 0)
            avg_return = sum(safe_get(s, 'result_24h') for s in signals_in_range) / len(signals_in_range)
            vol_stats[range_name] = {
                'count': len(signals_in_range),
                'win_rate': (wins / len(signals_in_range)) * 100,
                'avg_return': avg_return,
            }
    
    return vol_stats


def analyze_by_rsi(signals):
    """تحلیل بر اساس RSI"""
    ranges = {
        '0-30': [], '30-40': [], '40-50': [],
        '50-60': [], '60-70': [], '70+': [],
    }
    
    for s in signals:
        rsi = safe_get(s, 'rsi', 50)
        
        if rsi < 30:
            ranges['0-30'].append(s)
        elif rsi < 40:
            ranges['30-40'].append(s)
        elif rsi < 50:
            ranges['40-50'].append(s)
        elif rsi < 60:
            ranges['50-60'].append(s)
        elif rsi < 70:
            ranges['60-70'].append(s)
        else:
            ranges['70+'].append(s)
    
    rsi_stats = {}
    for range_name, signals_in_range in ranges.items():
        if signals_in_range:
            wins = sum(1 for s in signals_in_range if safe_get(s, 'result_24h') > 0)
            avg_return = sum(safe_get(s, 'result_24h') for s in signals_in_range) / len(signals_in_range)
            rsi_stats[range_name] = {
                'count': len(signals_in_range),
                'win_rate': (wins / len(signals_in_range)) * 100,
                'avg_return': avg_return,
            }
    
    return rsi_stats


def generate_report(metrics, pattern_stats, score_stats, z_stats, vol_stats, rsi_stats):
    """تولید گزارش جامع"""
    report = f"""
╔══════════════════════════════════════════════════════════════╗
║          CRYPTO-AGENT v15 - BACKTEST REPORT                 ║
╚══════════════════════════════════════════════════════════════╝

 PERFORMANCE METRICS
──────────────────────────────────────────────────────────────
Total Signals:     {metrics['total_signals']:>6}
Wins (24h):        {metrics['wins_24h']:>6}
Wins (48h):        {metrics['wins_48h']:>6}
Pumps Detected:    {metrics['pumps_detected']:>6}

Win Rate (24h):    {metrics['win_rate_24h']:>6.2f}%
Win Rate (48h):    {metrics['win_rate_48h']:>6.2f}%

Avg Return (24h):  {metrics['avg_return_24h']:>+7.2f}%
Avg Return (48h):  {metrics['avg_return_48h']:>+7.2f}%

Total Return (24h):{metrics['total_return_24h']:>+7.2f}%
Total Return (48h):{metrics['total_return_48h']:>+7.2f}%

Profit Factor:     {metrics['profit_factor']:>6.2f}
Sharpe Ratio:      {metrics['sharpe_ratio']:>6.2f}
Max Drawdown:      {metrics['max_drawdown']:>6.2f}%

Gross Profit:      {metrics['gross_profit_24h']:>+7.2f}%
Gross Loss:        {metrics['gross_loss_24h']:>7.2f}%

🎯 EVALUATION
──────────────────────────────────────────────────────────────
"""
    
    # ارزیابی
    if metrics['win_rate_24h'] >= 55:
        report += "✅ Win Rate: GOOD (≥55%)\n"
    else:
        report += f"❌ Win Rate: NEEDS IMPROVEMENT ({metrics['win_rate_24h']:.1f}% < 55%)\n"
    
    if metrics['profit_factor'] >= 1.5:
        report += "✅ Profit Factor: GOOD (≥1.5)\n"
    else:
        report += f"❌ Profit Factor: NEEDS IMPROVEMENT ({metrics['profit_factor']:.2f} < 1.5)\n"
    
    if metrics['max_drawdown'] <= 20:
        report += "✅ Max Drawdown: GOOD (≤20%)\n"
    else:
        report += f"❌ Max Drawdown: NEEDS IMPROVEMENT ({metrics['max_drawdown']:.1f}% > 20%)\n"
    
    if metrics['sharpe_ratio'] >= 1.0:
        report += "✅ Sharpe Ratio: GOOD (≥1.0)\n"
    else:
        report += f"❌ Sharpe Ratio: NEEDS IMPROVEMENT ({metrics['sharpe_ratio']:.2f} < 1.0)\n"
    
    # تحلیل الگوها
    report += "\n🔍 PATTERN ANALYSIS\n"
    report += "──────────────────────────────────────────────────────────────\n"
    sorted_patterns = sorted(pattern_stats.items(), key=lambda x: x[1]['win_rate'], reverse=True)
    for pattern, stats in sorted_patterns:
        report += f"{pattern:>20s}: {stats['win_rate']:>5.1f}% win rate, {stats['count']:>3} signals, {stats['avg_return']:>+6.2f}% avg\n"
    
    # تحلیل امتیاز (new_score یا score)
    report += "\n📈 SCORE RANGE ANALYSIS\n"
    report += "──────────────────────────────────────────────────────────────\n"
    for range_name in ['90-100', '80-89', '70-79', '60-69', '50-59', '40-49', '30-39', '0-29']:
        if range_name in score_stats:
            stats = score_stats[range_name]
            report += f"Score {range_name:>6s}: {stats['win_rate']:>5.1f}% win rate, {stats['count']:>3} signals, {stats['avg_return']:>+6.2f}% avg\n"
    
    # تحلیل Z-Score
    report += "\n📊 Z-SCORE ANALYSIS\n"
    report += "──────────────────────────────────────────────────────────────\n"
    for range_name in ['0.0-0.5', '0.5-1.0', '1.0-1.5', '1.5-2.0', '2.0-3.0', '3.0+']:
        if range_name in z_stats:
            stats = z_stats[range_name]
            report += f"Z-Score {range_name:>6s}: {stats['win_rate']:>5.1f}% win rate, {stats['count']:>3} signals, {stats['avg_return']:>+6.2f}% avg\n"
    
    # تحلیل Volume Multiplier
    report += "\n📊 VOLUME MULTIPLIER ANALYSIS\n"
    report += "──────────────────────────────────────────────────────────────\n"
    for range_name in ['1.0-1.5', '1.5-2.0', '2.0-3.0', '3.0-5.0', '5.0+']:
        if range_name in vol_stats:
            stats = vol_stats[range_name]
            report += f"Vol Mult {range_name:>6s}: {stats['win_rate']:>5.1f}% win rate, {stats['count']:>3} signals, {stats['avg_return']:>+6.2f}% avg\n"
    
    # تحلیل RSI
    report += "\n📊 RSI ANALYSIS\n"
    report += "──────────────────────────────────────────────────────────────\n"
    for range_name in ['0-30', '30-40', '40-50', '50-60', '60-70', '70+']:
        if range_name in rsi_stats:
            stats = rsi_stats[range_name]
            report += f"RSI {range_name:>6s}: {stats['win_rate']:>5.1f}% win rate, {stats['count']:>3} signals, {stats['avg_return']:>+6.2f}% avg\n"
    
    report += "\n" + "="*62 + "\n"
    
    return report


def main():
    """اجرای اصلی"""
    print("🚀 Loading signals from database...\n")
    
    signals = load_signals()
    ml_features = load_ml_features()
    
    print(f" Loaded {len(signals)} signals with results")
    print(f"📊 Loaded {len(ml_features)} ML features with labels\n")
    
    if not signals:
        print("❌ No signals with results found in database")
        return
    
    # محاسبه معیارها
    metrics = calculate_metrics(signals)
    
    # تحلیل‌ها
    pattern_stats = analyze_by_pattern(signals)
    score_stats = analyze_by_score_range(signals)
    z_stats = analyze_by_z_score(signals)
    vol_stats = analyze_by_volume_mult(signals)
    rsi_stats = analyze_by_rsi(signals)
    
    # تولید گزارش
    report = generate_report(metrics, pattern_stats, score_stats, z_stats, vol_stats, rsi_stats)
    print(report)
    
    # ذخیره نتایج
    output_path = Path(__file__).parent / "backtest_results.json"
    results = {
        'metrics': metrics,
        'pattern_analysis': pattern_stats,
        'score_analysis': score_stats,
        'z_score_analysis': z_stats,
        'volume_mult_analysis': vol_stats,
        'rsi_analysis': rsi_stats,
        'timestamp': datetime.now().isoformat(),
    }
    
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"💾 Results saved to {output_path}")


if __name__ == "__main__":
    main()