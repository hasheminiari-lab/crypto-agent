"""
جمع‌آوری داده برای آموزش ML
"""
import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("Collector")
DB_PATH = Path(__file__).parent / "bot.db"

FEATURES_LIST = [
    'z_1h', 'z_4h', 'z_1d',
    'vol_mult_1h', 'vol_mult_4h',
    'change_24h', 'price',
    'rsi', 'macd_hist', 'macd_cross',
    'bollinger_pctb', 'atr_ratio',
    'volume_24h', 'liquidity', 'market_cap',
    'funding_rate', 'oi_change',
    'accumulation', 'divergence', 'stop_hunt',
    'price_acceleration', 'whale_dist', 'breakout',
    'social_score', 'tier_z'
]

def init_ml_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS ml_features (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coin TEXT,
        timestamp TEXT,
        z_1h REAL, z_4h REAL, z_1d REAL,
        vol_mult_1h REAL, vol_mult_4h REAL,
        change_24h REAL, price REAL,
        rsi REAL, macd_hist REAL, macd_cross INTEGER,
        bollinger_pctb REAL, atr_ratio REAL,
        volume_24h REAL, liquidity REAL, market_cap REAL,
        funding_rate REAL, oi_change REAL,
        accumulation INTEGER, divergence INTEGER, stop_hunt INTEGER,
        price_acceleration INTEGER, whale_dist INTEGER, breakout INTEGER,
        social_score INTEGER, tier_z REAL,
        label_1h INTEGER DEFAULT NULL,
        label_6h INTEGER DEFAULT NULL,
        label_24h INTEGER DEFAULT NULL,
        max_gain_24h REAL DEFAULT NULL,
        raw_features TEXT
    )""")
    conn.commit()
    conn.close()
    logger.info("🧠 ML DB initialized")

def save_ml_features(coin_data: dict):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # جلوگیری از تکراری در همان ساعت
    hour_ts = coin_data.get('timestamp', '')[:13]
    c.execute("SELECT id FROM ml_features WHERE coin=? AND timestamp LIKE ?",
              (coin_data['coin'], f"{hour_ts}%"))
    if c.fetchone():
        conn.close()
        return
    
    raw = json.dumps(coin_data, ensure_ascii=False, default=str)
    
    # ✅ 28 ستون = 28 مقدار = 28 علامت سوال
    c.execute("""INSERT INTO ml_features (
        coin, timestamp,
        z_1h, z_4h, z_1d, vol_mult_1h, vol_mult_4h,
        change_24h, price,
        rsi, macd_hist, macd_cross, bollinger_pctb, atr_ratio,
        volume_24h, liquidity, market_cap,
        funding_rate, oi_change,
        accumulation, divergence, stop_hunt,
        price_acceleration, whale_dist, breakout,
        social_score, tier_z,
        raw_features
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        coin_data.get('coin', ''),
        coin_data.get('timestamp', datetime.now().isoformat()),
        coin_data.get('z_1h', 0),
        coin_data.get('z_4h', 0),
        coin_data.get('z_1d', 0),
        coin_data.get('vol_mult_1h', 0),
        coin_data.get('vol_mult_4h', 0),
        coin_data.get('change_24h', 0),
        coin_data.get('price', 0),
        coin_data.get('rsi', 50),
        coin_data.get('macd_hist', 0),
        coin_data.get('macd_cross', 0),
        coin_data.get('bollinger_pctb', 0.5),
        coin_data.get('atr_ratio', 0.02),
        coin_data.get('volume_24h', 0),
        coin_data.get('liquidity', 0),
        coin_data.get('market_cap', 0),
        coin_data.get('funding_rate', 0),
        coin_data.get('oi_change', 0),
        coin_data.get('accumulation', 0),
        coin_data.get('divergence', 0),
        coin_data.get('stop_hunt', 0),
        coin_data.get('price_acceleration', 0),
        coin_data.get('whale_dist', 0),
        coin_data.get('breakout', 0),
        coin_data.get('social_score', 0),
        coin_data.get('tier_z', 2.0),
        raw
    ))
    conn.commit()
    conn.close()

def label_signals():
    """برچسب‌گذاری سیگنال‌های قدیمی"""
    import requests
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("""SELECT id, coin, price, timestamp FROM ml_features 
                 WHERE label_24h IS NULL 
                 AND timestamp < datetime('now', '-24 hours')
                 LIMIT 50""")
    rows = c.fetchall()
    
    labeled = 0
    for row in rows:
        sig_id, coin, entry_price, ts = row
        if not coin or entry_price <= 0:
            continue
        
        current_price = None
        try:
            r = requests.get(
                f"https://api.binance.com/api/v3/ticker/price?symbol={coin}USDT",
                timeout=5
            )
            if r.status_code == 200:
                current_price = float(r.json()['price'])
        except:
            pass
        
        if current_price is None:
            try:
                r = requests.get(
                    f"https://api.dexscreener.com/latest/dex/search?q={coin}",
                    timeout=5
                )
                if r.status_code == 200:
                    pairs = r.json().get('pairs', [])
                    if pairs:
                        current_price = float(pairs[0].get('priceUsd', 0) or 0)
            except:
                pass
        
        if current_price and current_price > 0:
            gain = ((current_price - entry_price) / entry_price) * 100
            
            label_1h = 1 if gain > 20 else 0
            label_6h = 1 if gain > 30 else 0
            label_24h = 1 if gain > 50 else 0
            
            c.execute("""UPDATE ml_features 
                        SET label_1h=?, label_6h=?, label_24h=?, max_gain_24h=?
                        WHERE id=?""",
                      (label_1h, label_6h, label_24h, round(gain, 2), sig_id))
            labeled += 1
    
    conn.commit()
    conn.close()
    if labeled > 0:
        logger.info(f"🏷️ Labeled {labeled} signals")
    return labeled

def get_ml_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM ml_features")
    total = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM ml_features WHERE label_24h IS NOT NULL")
    labeled = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM ml_features WHERE label_24h = 1")
    pumps = c.fetchone()[0]
    
    conn.close()
    
    return {
        'total': total,
        'labeled': labeled,
        'pumps': pumps,
        'progress': min(100, (labeled / 500 * 100))
    }