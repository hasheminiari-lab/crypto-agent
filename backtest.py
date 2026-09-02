import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict
import time

class BacktestEngine:
    """موتور بک‌تست برای ارزیابی استراتژی"""
    
    def __init__(self):
        self.binance_url = "https://api.binance.com/api/v3"
        
    def get_historical_data(self, symbol: str, interval: str = '1h', 
                           days: int = 30) -> List[Dict]:
        """دریافت داده‌های تاریخی از Binance"""
        try:
            end_time = int(time.time() * 1000)
            start_time = end_time - (days * 24 * 60 * 60 * 1000)
            
            url = f"{self.binance_url}/klines"
            params = {
                'symbol': f"{symbol}USDT",
                'interval': interval,
                'startTime': start_time,
                'endTime': end_time,
                'limit': 1000
            }
            
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                return []
            
            data = response.json()
            return [{
                'timestamp': k[0],
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5]),
                'quote_volume': float(k[7])
            } for k in data]
        except Exception as e:
            print(f"Error fetching data for {symbol}: {e}")
            return []
    
    def calculate_z_score(self, volumes: List[float], current_idx: int) -> float:
        """محاسبه Z-Score حجم"""
        if current_idx < 24:
            return 0
        historical_volumes = volumes[max(0, current_idx-24):current_idx]
        if len(historical_volumes) < 20:
            return 0
        mean = np.mean(historical_volumes)
        std = np.std(historical_volumes)
        if std == 0:
            return 0
        return (volumes[current_idx] - mean) / std
    
    def simulate_trade(self, entry_price: float, stop_loss: float, 
                      take_profit: float, candles: List[Dict]) -> Dict:
        """شبیه‌سازی یک معامله"""
        result = {
            'entry_price': entry_price,
            'exit_price': None,
            'pnl_percent': 0,
            'exit_reason': 'timeout',
            'hold_time': 0
        }
        
        for i, candle in enumerate(candles):
            high = candle['high']
            low = candle['low']
            
            # بررسی استاپ لاس
            if low <= stop_loss:
                result['exit_price'] = stop_loss
                result['pnl_percent'] = ((stop_loss - entry_price) / entry_price) * 100
                result['exit_reason'] = 'stop_loss'
                result['hold_time'] = i
                return result
            
            # بررسی تارگت
            if high >= take_profit:
                result['exit_price'] = take_profit
                result['pnl_percent'] = ((take_profit - entry_price) / entry_price) * 100
                result['exit_reason'] = 'take_profit'
                result['hold_time'] = i
                return result
        
        # اگر نه استاپ خورد نه تارگت
        result['exit_price'] = candles[-1]['close']
        result['pnl_percent'] = ((candles[-1]['close'] - entry_price) / entry_price) * 100
        result['hold_time'] = len(candles)
        return result
    
    def run_backtest(self, symbol: str, z_threshold: float = 2.0, 
                    days: int = 30) -> Dict:
        """اجرای بک‌تست روی یک کوین"""
        print(f"Backtesting {symbol}...")
        
        candles = self.get_historical_data(symbol, '1h', days)
        if len(candles) < 48:
            return {'error': 'Not enough data'}
        
        volumes = [c['quote_volume'] for c in candles]
        trades = []
        
        # اسکن برای یافتن سیگنال‌ها
        for i in range(24, len(candles) - 24):
            z_score = self.calculate_z_score(volumes, i)
            
            if z_score >= z_threshold:
                entry_price = candles[i]['close']
                stop_loss = entry_price * 0.85  # 15% استاپ
                take_profit = entry_price * 1.30  # 30% تارگت
                
                # شبیه‌سازی معامله از این نقطه به بعد
                future_candles = candles[i+1:i+49]  # 48 ساعت آینده
                if len(future_candles) >= 24:
                    trade_result = self.simulate_trade(entry_price, stop_loss, 
                                                      take_profit, future_candles)
                    trade_result['z_score'] = z_score
                    trade_result['entry_time'] = candles[i]['timestamp']
                    trades.append(trade_result)
        
        # محاسبه آمار
        if not trades:
            return {'total_trades': 0, 'win_rate': 0}
        
        wins = [t for t in trades if t['pnl_percent'] > 0]
        losses = [t for t in trades if t['pnl_percent'] <= 0]
        
        return {
            'symbol': symbol,
            'total_trades': len(trades),
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': len(wins) / len(trades) * 100 if trades else 0,
            'avg_win': np.mean([t['pnl_percent'] for t in wins]) if wins else 0,
            'avg_loss': np.mean([t['pnl_percent'] for t in losses]) if losses else 0,
            'max_win': max([t['pnl_percent'] for t in trades]) if trades else 0,
            'max_loss': min([t['pnl_percent'] for t in trades]) if trades else 0,
            'avg_hold_time': np.mean([t['hold_time'] for t in trades]) if trades else 0,
            'trades': trades
        }

def run_backtest() -> str:
    """اجرای بک‌تست روی چند کوین معروف"""
    engine = BacktestEngine()
    
    # لیست کوین‌های معروف با پامپ‌های تاریخی
    test_coins = ['SOL', 'AVAX', 'MATIC', 'LINK', 'DOT', 'NEAR', 'APT', 'ARB', 'OP', 'TIA']
    
    results = []
    for coin in test_coins:
        result = engine.run_backtest(coin, z_threshold=2.0, days=30)
        if 'error' not in result:
            results.append(result)
            print(f"{coin}: {result['total_trades']} trades, Win Rate: {result['win_rate']:.1f}%")
    
    if not results:
        return "❌ هیچ داده‌ای برای بک‌تست یافت نشد"
    
    # محاسبه آمار کلی
    total_trades = sum(r['total_trades'] for r in results)
    total_wins = sum(r['wins'] for r in results)
    overall_win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
    
    avg_win = np.mean([r['avg_win'] for r in results if r['avg_win'] > 0]) if results else 0
    avg_loss = np.mean([r['avg_loss'] for r in results if r['avg_loss'] < 0]) if results else 0
    
    report = "🧪 <b>نتایج بک‌تست (30 روز گذشته)</b>\n\n"
    report += f"📊 <b>آمار کلی:</b>\n"
    report += f"• تعداد کل معاملات: {total_trades}\n"
    report += f"• Win Rate: {overall_win_rate:.1f}%\n"
    report += f"• میانگین سود: {avg_win:+.2f}%\n"
    report += f"• میانگین ضرر: {avg_loss:+.2f}%\n"
    report += f"• میانگین زمان نگهداری: {np.mean([r['avg_hold_time'] for r in results]):.1f} ساعت\n\n"
    
    report += f"📈 <b>نتایج به تفکیک کوین:</b>\n"
    for r in results:
        report += f"• <b>{r['symbol']}</b>: {r['total_trades']} معامله | Win Rate: {r['win_rate']:.1f}%\n"
    
    report += f"\n⚠️ <i>این نتایج بر اساس داده‌های گذشته است و تضمینی برای آینده نیست.</i>"
    
    return report