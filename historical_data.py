"""
دانلود داده تاریخی از Binance برای آموزش فوری مدل
با برچسب‌گذاری واقعی بر اساس نتیجه 24 ساعت بعد
"""
import requests
import time
import sqlite3
import json
import logging
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

from collector import init_ml_db

logger = logging.getLogger("HistoricalData")
DB_PATH = Path(__file__).parent / "bot.db"

COINS = [
    'BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'AVAX', 'DOT',
    'LINK', 'UNI', 'LTC', 'BCH', 'ATOM', 'FIL', 'ETC', 'NEAR', 'APT', 'ARB',
    'OP', 'SUI', 'SEI', 'TIA', 'INJ', 'FET', 'AAVE', 'PEPE', 'SHIB',
    'TRX', 'ICP', 'ALGO', 'XLM', 'VET', 'SUSHI', 'GRT', 'IMX', 'GALA',
    'SAND', 'MANA', 'CRV', 'COMP', 'SNX', 'YFI', '1INCH', 'ENJ', 'CHZ',
    'BAT', 'ZRX', 'STORJ', 'SKL', 'CELR', 'COTI', 'ANKR', 'BAND'
]


def get_db_connection():
    """✅ ساخت connection با timeout طولانی"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")  # ✅ حالت WAL برای عملکرد بهتر
    conn.execute("PRAGMA busy_timeout=30000")  # ✅ 30 ثانیه timeout
    return conn


def download_klines(symbol, interval='1h', days=90):
    """دانلود 90 روز برای داشتن 24 ساعت بعد برای برچسب"""
    end_time = int(time.time() * 1000)
    start_time = end_time - (days * 24 * 60 * 60 * 1000)
    all_data = []
    current_start = start_time
    
    while current_start < end_time:
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={
                    'symbol': f"{symbol}USDT",
                    'interval': interval,
                    'startTime': current_start,
                    'limit': 1000
                },
                timeout=10
            )
            if r.status_code != 200:
                break
            data = r.json()
            if not data:
                break
            all_data.extend(data)
            current_start = data[-1][0] + 1
            time.sleep(0.1)
        except Exception as e:
            logger.warning(f"Error downloading {symbol}: {e}")
            break
    return all_data


def calculate_features_from_klines(klines, coin_symbol):
    """محاسبه ویژگی‌ها + برچسب واقعی 24 ساعت بعد"""
    if len(klines) < 74:
        return []
    
    df = pd.DataFrame(klines, columns=[
        'ts', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
        'taker_buy_quote', 'ignore'
    ])
    
    for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    df = df.dropna()
    if len(df) < 74:
        return []
    
    features_list = []
    
    for i in range(50, len(df) - 24):
        window = df.iloc[max(0, i-50):i]
        current = df.iloc[i]
        future = df.iloc[i + 24]
        
        vol_mean = window['volume'].mean()
        vol_std = window['volume'].std()
        z_1h = (current['volume'] - vol_mean) / vol_std if vol_std > 0 else 0
        vol_mult = current['volume'] / vol_mean if vol_mean > 0 else 0
        
        change = ((current['close'] - df.iloc[i-1]['close']) / df.iloc[i-1]['close'] * 100) if i > 0 else 0
        change_24h = ((current['close'] - df.iloc[max(0, i-24)]['close']) / df.iloc[max(0, i-24)]['close'] * 100) if i >= 24 else 0
        
        deltas = df['close'].iloc[max(0, i-15):i].diff().dropna()
        gains = deltas[deltas > 0].mean() if len(deltas[deltas > 0]) > 0 else 0
        losses = -deltas[deltas < 0].mean() if len(deltas[deltas < 0]) > 0 else 0.001
        rsi = 100 - (100 / (1 + gains / losses))
        
        ema12 = df['close'].iloc[max(0, i-26):i].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].iloc[max(0, i-26):i].ewm(span=26, adjust=False).mean()
        macd_hist = (ema12.iloc[-1] - ema26.iloc[-1]) if len(ema12) > 0 else 0
        
        bb_mean = window['close'].mean()
        bb_std = window['close'].std()
        bb_upper = bb_mean + 2 * bb_std
        bb_lower = bb_mean - 2 * bb_std
        bollinger_pctb = (current['close'] - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5
        
        high_low = window['high'] - window['low']
        atr = high_low.mean()
        atr_ratio = atr / current['close'] if current['close'] > 0 else 0.02
        
        recent = df['close'].iloc[max(0, i-10):i]
        accumulation = 1 if (recent.max() - recent.min()) / current['close'] < 0.02 else 0
        price_acceleration = 1 if abs(change) > 10 else 0
        breakout = 1 if (change > 20 and vol_mult > 2) else 0
        
        market_cap = current['quote_volume'] * 20
        
        future_gain = ((future['close'] - current['close']) / current['close'] * 100)
        label_24h = 1 if future_gain > 20 else 0
        
        ts_int = int(current['ts'])
        base_ts = datetime.fromtimestamp(ts_int / 1000)
        unique_ts = (base_ts + timedelta(seconds=hash(coin_symbol) % 3600)).isoformat()
        
        features = {
            'coin': 'HIST',
            'timestamp': unique_ts,
            'z_1h': float(z_1h),
            'z_4h': float(z_1h * 0.7),
            'z_1d': float(z_1h * 0.5),
            'vol_mult_1h': float(vol_mult),
            'vol_mult_4h': float(vol_mult * 0.8),
            'change_24h': float(change_24h),
            'price': float(current['close']),
            'rsi': float(rsi),
            'macd_hist': float(macd_hist),
            'macd_cross': 0,
            'bollinger_pctb': float(bollinger_pctb),
            'atr_ratio': float(atr_ratio),
            'volume_24h': float(current['quote_volume']),
            'liquidity': float(current['quote_volume']),
            'market_cap': float(market_cap),
            'funding_rate': 0,
            'oi_change': 0,
            'accumulation': int(accumulation),
            'divergence': 0,
            'stop_hunt': 0,
            'price_acceleration': int(price_acceleration),
            'whale_dist': 0,
            'breakout': int(breakout),
            'social_score': 0,
            'tier_z': 1.5,
            'label_24h': label_24h,
            'future_gain': float(future_gain),
            'raw_features': json.dumps({'symbol': coin_symbol, 'ts': ts_int})
        }
        features_list.append(features)
    
    return features_list


def save_historical_features(features_list):
    """✅ ذخیره با connection management صحیح"""
    conn = get_db_connection()
    try:
        c = conn.cursor()
        saved = 0
        for feat in features_list:
            raw = json.dumps(feat, ensure_ascii=False, default=str)
            
            c.execute("SELECT id FROM ml_features WHERE coin='HIST' AND timestamp=?",
                      (feat['timestamp'],))
            if c.fetchone():
                continue
            
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
                label_24h,
                raw_features
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                feat['coin'], feat['timestamp'],
                feat['z_1h'], feat['z_4h'], feat['z_1d'],
                feat['vol_mult_1h'], feat['vol_mult_4h'],
                feat['change_24h'], feat['price'],
                feat['rsi'], feat['macd_hist'], feat['macd_cross'],
                feat['bollinger_pctb'], feat['atr_ratio'],
                feat['volume_24h'], feat['liquidity'], feat['market_cap'],
                feat['funding_rate'], feat['oi_change'],
                feat['accumulation'], feat['divergence'], feat['stop_hunt'],
                feat['price_acceleration'], feat['whale_dist'], feat['breakout'],
                feat['social_score'], feat['tier_z'],
                feat['label_24h'],
                raw
            ))
            saved += 1
            
            # ✅ commit هر 100 رکورد برای جلوگیری از قفل طولانی
            if saved % 100 == 0:
                conn.commit()
        
        conn.commit()
        return saved
    except Exception as e:
        logger.error(f"Error saving features: {e}")
        conn.rollback()
        return 0
    finally:
        conn.close()


def collect_all_historical():
    init_ml_db()
    
    # ✅ پاک کردن با connection management صحیح
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM ml_features WHERE coin='HIST'")
        conn.commit()
        logger.info("🗑️ داده‌های HIST قبلی پاک شدند")
    except Exception as e:
        logger.error(f"Error deleting: {e}")
    finally:
        conn.close()
    
    logger.info(" شروع جمع‌آوری داده تاریخی...")
    total_saved = 0
    
    for i, coin in enumerate(COINS):
        logger.info(f"[{i+1}/{len(COINS)}] دانلود {coin}...")
        
        klines = download_klines(coin, interval='1h', days=90)
        if not klines:
            logger.warning(f"  ⚠️ داده‌ای برای {coin} یافت نشد")
            continue
        
        features = calculate_features_from_klines(klines, coin)
        if not features:
            continue
        
        saved = save_historical_features(features)
        total_saved += saved
        logger.info(f"  ✅ {saved} نمونه از {coin}")
        
        time.sleep(0.5)
    
    logger.info(f"✅ جمع‌آوری کامل: {total_saved} نمونه با برچسب واقعی")
    return total_saved


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    collect_all_historical()