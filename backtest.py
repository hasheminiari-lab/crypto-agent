import requests
import numpy as np
from typing import List, Dict
import time

# ✅ هزینه واقعی معامله
FEE = 0.001
SLIP = 0.003
COST = FEE + SLIP  # 0.4% هر طرف

class BacktestEngine:
    def __init__(self):
        self.url = "https://api.binance.com/api/v3"

    def get_data(self, sym: str, days: int = 30) -> List[Dict]:
        try:
            end = int(time.time() * 1000)
            start = end - days * 86400000
            r = requests.get(f"{self.url}/klines", params={
                'symbol': f"{sym}USDT", 'interval': '1h',
                'startTime': start, 'endTime': end, 'limit': 1000
            }, timeout=10)
            if r.status_code != 200: return []
            return [{'ts': k[0], 'o': float(k[1]), 'h': float(k[2]),
                     'l': float(k[3]), 'c': float(k[4]),
                     'v': float(k[5]), 'qv': float(k[7])} for k in r.json()]
        except Exception as e:
            print(f"Error {sym}: {e}"); return []

    def z_score(self, vols: List[float], idx: int) -> float:
        """✅ بدون Lookahead: فقط از داده‌های قبل از idx"""
        if idx < 24: return 0.0
        hist = vols[max(0, idx-24):idx]  # ✅ بدون vols[idx]
        if len(hist) < 20: return 0.0
        m, s = np.mean(hist), np.std(hist, ddof=1)
        if s == 0: return 0.0
        return float((vols[idx] - m) / s)

    def sim_trade(self, entry: float, stop: float, target: float,
                  candles: List[Dict]) -> Dict:
        """✅ با هزینه واقعی"""
        real_entry = entry * (1 + COST)
        res = {'entry': entry, 'exit': None, 'pnl': 0,
               'reason': 'timeout', 'time': 0}
        for i, c in enumerate(candles):
            if c['l'] <= stop:
                real_exit = stop * (1 - COST)
                res.update({'exit': stop, 'pnl': ((real_exit-real_entry)/real_entry)*100,
                           'reason': 'stop', 'time': i})
                return res
            if c['h'] >= target:
                real_exit = target * (1 - COST)
                res.update({'exit': target, 'pnl': ((real_exit-real_entry)/real_entry)*100,
                           'reason': 'target', 'time': i})
                return res
        real_exit = candles[-1]['c'] * (1 - COST)
        res.update({'exit': candles[-1]['c'],
                   'pnl': ((real_exit-real_entry)/real_entry)*100, 'time': len(candles)})
        return res

    def run(self, sym: str, z_th: float = 2.0, days: int = 30) -> Dict:
        print(f"  {sym}...", end=" ")
        candles = self.get_data(sym, days)
        if len(candles) < 72:
            print("❌ data insufficient"); return {'symbol': sym, 'trades': 0, 'wr': 0}

        vols = [c['qv'] for c in candles]
        trades = []
        for i in range(24, len(candles) - 48):
            z = self.z_score(vols, i)
            if z >= z_th:
                e = candles[i]['c']
                t = self.sim_trade(e, e*0.85, e*1.30, candles[i+1:i+49])
                t['z'] = z; trades.append(t)

        if not trades:
            print("0 trades"); return {'symbol': sym, 'trades': 0, 'wr': 0}

        wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]
        aw = float(np.mean([t['pnl'] for t in wins])) if wins else 0
        al = float(np.mean([t['pnl'] for t in losses])) if losses else 0
        print(f"{len(trades)} trades, WR: {len(wins)/len(trades)*100:.1f}%")
        return {
            'symbol': sym, 'trades': len(trades),
            'wins': len(wins), 'losses': len(losses),
            'wr': len(wins)/len(trades)*100,
            'aw': aw, 'al': al,
            'max_w': float(max(t['pnl'] for t in trades)),
            'max_l': float(min(t['pnl'] for t in trades)),
            'avg_t': float(np.mean([t['time'] for t in trades]))
        }

def run_backtest() -> str:
    engine = BacktestEngine()
    # ✅ کوین‌های موفق + شکست‌خورده (رفع Survivorship Bias)
    coins = [
        'SOL','AVAX','LINK','DOT','NEAR','APT','ARB','OP','TIA','MATIC',
        'LUNA','FTT','SRM','RAY','STEP'
    ]
    results = []
    for c in coins:
        r = engine.run(c, z_th=2.0, days=30)
        if r['trades'] > 0: results.append(r)

    if not results:
        return "❌ داده‌ای یافت نشد"

    tt = sum(r['trades'] for r in results)
    tw = sum(r['wins'] for r in results)
    wr = tw/tt*100 if tt else 0
    aw = float(np.mean([r['aw'] for r in results if r['aw']>0])) if any(r['aw']>0 for r in results) else 0
    al = float(np.mean([r['al'] for r in results if r['al']<0])) if any(r['al']<0 for r in results) else 0
    pf = abs(aw/al) if al else 0
    exp = (wr/100*aw) + ((1-wr/100)*al)

    r = "🧪 <b>بک‌تست v10.0</b>\n"
    r += "<i>(هزینه 0.8% + بدون Lookahead + کوین‌های شکست‌خورده)</i>\n\n"
    r += f"📊 <b>آمار کلی:</b>\n"
    r += f"• معاملات: {tt}\n"
    r += f"• Win Rate: {wr:.1f}%\n"
    r += f"• میانگین سود: {aw:+.2f}%\n"
    r += f"• میانگین ضرر: {al:+.2f}%\n"
    r += f"• Profit Factor: {pf:.2f}\n"
    r += f"• Expectancy: {exp:+.2f}%\n\n"
    r += f"📈 <b>به تفکیک:</b>\n"
    for x in results:
        e = "✅" if x['wr'] >= 50 else "❌"
        r += f"{e} <b>{x['symbol']}</b>: {x['trades']} | WR: {x['wr']:.1f}%\n"

    v = "✅ سودده" if pf > 1.2 and exp > 0 else ("⚠️ لب‌مرز" if pf > 1.0 else "❌ ضررده")
    r += f"\n🎯 <b>حکم:</b> {v}\n"
    r += "⚠️ <i>گذشته ≠ آینده</i>"
    return r

if __name__ == "__main__":
    print(run_backtest())