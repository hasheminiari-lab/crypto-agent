import os
import re
import sqlite3
import asyncio
import time
import requests
import logging
import threading
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, timezone
from pathlib import Path
from html import escape
from collections import defaultdict
from typing import Optional, Dict, List, Tuple
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import numpy as np
import jdatetime
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# Import از فایل‌های دیگر
try:
    from risk_manager import RiskManager, PositionSizer
except ImportError:
    class RiskManager:
        def __init__(self, balance, risk_pct, max_pos):
            self.balance = balance
            self.risk_pct = risk_pct
            self.max_pos = max_pos
        
        def calculate_position_size(self, entry, stop, z_score):
            risk_amount = self.balance * (self.risk_pct / 100)
            risk_per_unit = abs(entry - stop)
            if risk_per_unit == 0:
                return 0
            return min(risk_amount / risk_per_unit * entry, self.balance * 0.1)
        
        def calculate_risk_reward(self, entry, stop, target):
            risk = abs(entry - stop)
            reward = abs(target - entry)
            return reward / risk if risk > 0 else 0

load_dotenv()

# ==========================================
# ⚙️ تنظیمات اصلی
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
ALLOWED_USERS = set(map(int, os.getenv("ALLOWED_USERS", "").split(","))) if os.getenv("ALLOWED_USERS") else {ADMIN_CHAT_ID}
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")

ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "10000"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "2.0"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "5"))

if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ TELEGRAM_TOKEN در فایل .env تنظیم نشده!")

SCAN_INTERVAL_MINUTES = 5
FULL_ANALYSIS_INTERVAL_MINUTES = 30
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "bot.db"

# ==========================================
# 📝 تنظیمات لاگ
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('bot.log', maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

EXCHANGES = {
    'binance': 'https://api.binance.com/api/v3',
    'binance_futures': 'https://fapi.binance.com/fapi/v1',
    'okx': 'https://www.okx.com/api/v5',
    'mexc': 'https://api.mexc.com/api/v3',
    'bybit': 'https://api.bybit.com/v5'
}

COINGECKO_URL = 'https://api.coingecko.com/api/v3'

COINGECKO_ID_MAP = {
    'BTC': 'bitcoin', 'ETH': 'ethereum', 'BNB': 'binancecoin', 'SOL': 'solana',
    'XRP': 'ripple', 'ADA': 'cardano', 'DOGE': 'dogecoin', 'AVAX': 'avalanche-2',
    'DOT': 'polkadot', 'MATIC': 'matic-network', 'LINK': 'chainlink', 'UNI': 'uniswap',
    'LTC': 'litecoin', 'BCH': 'bitcoin-cash', 'ATOM': 'cosmos', 'FIL': 'filecoin',
    'ETC': 'ethereum-classic', 'NEAR': 'near', 'APT': 'aptos', 'ARB': 'arbitrum',
    'OP': 'optimism', 'SUI': 'sui', 'SEI': 'sei-network', 'TIA': 'celestia',
    'INJ': 'injective-protocol', 'FET': 'fetch-ai', 'RNDR': 'render-token',
    'AAVE': 'aave', 'MKR': 'maker', 'SNX': 'havven', 'COMP': 'compound-governance-token',
    'CRV': 'curve-dao-token', 'SUSHI': 'sushi', 'YFI': 'yearn-finance', 'PEPE': 'pepe',
    'SHIB': 'shiba-inu', 'WIF': 'dogwifcoin', 'BONK': 'bonk', 'JUP': 'jupiter-exchange-solana',
    'PYTH': 'pyth-network', 'STRK': 'starknet', 'ORDI': 'ordi', 'MANTA': 'manta-network',
    'ACE': 'endurance', 'RONIN': 'ronin', 'GRT': 'the-graph', 'IMX': 'immutable-x'
}

# ==========================================
# 🎯 سیستم Tier-Based
# ==========================================
def get_tier_threshold(market_cap: float) -> Dict:
    if market_cap > 10_000_000_000:
        return {'tier_name': 'Mega', 'z_threshold': 1.0, 'vol_mult': 1.2, 'interval': '4h', 'limit': 42, 'min_vol': 50_000_000, 'slippage': 0.001}
    elif market_cap > 1_000_000_000:
        return {'tier_name': 'Large', 'z_threshold': 1.2, 'vol_mult': 1.5, 'interval': '4h', 'limit': 42, 'min_vol': 10_000_000, 'slippage': 0.003}
    elif market_cap > 100_000_000:
        return {'tier_name': 'Mid', 'z_threshold': 1.5, 'vol_mult': 2.0, 'interval': '1h', 'limit': 168, 'min_vol': 2_000_000, 'slippage': 0.005}
    else:
        return {'tier_name': 'Small', 'z_threshold': 2.0, 'vol_mult': 3.0, 'interval': '1h', 'limit': 168, 'min_vol': 200_000, 'slippage': 0.01}

def estimate_market_cap(quote_volume_24h: float) -> float:
    return quote_volume_24h * 20

# ==========================================
# 🕐 توابع کمکی زمان
# ==========================================
def get_time_info() -> Dict[str, str]:
    now_utc = datetime.now(timezone.utc)
    try:
        iran_tz = ZoneInfo("Asia/Tehran")
        now_iran = now_utc.astimezone(iran_tz)
    except:
        iran_tz = timezone(timedelta(hours=3, minutes=30))
        now_iran = now_utc.astimezone(iran_tz)
    now_hijri = jdatetime.datetime.fromgregorian(datetime=now_utc)
    return {
        "iran_time": now_iran.strftime("%Y-%m-%d %H:%M"),
        "hijri_time": now_hijri.strftime("%Y/%m/%d %H:%M"),
        "utc_time": now_utc.strftime("%Y-%m-%d %H:%M UTC")
    }

# ==========================================
# ⏱️ Rate Limiting
# ==========================================
_user_last_call: Dict[int, float] = defaultdict(float)
def rate_limited(user_id: int, min_interval: int = 30) -> bool:
    now = time.time()
    if now - _user_last_call[user_id] < min_interval: return False
    _user_last_call[user_id] = now
    return True

# ==========================================
# 🗄️ دیتابیس SQLite
# ==========================================
def _init_db_sync():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT UNIQUE, entry_price REAL, stop_loss REAL,
        take_profit_1 REAL, take_profit_2 REAL, take_profit_3 REAL, z_score REAL, score INTEGER,
        entry_time TEXT, status TEXT DEFAULT 'open', position_size REAL DEFAULT 0,
        risk_reward REAL DEFAULT 0
    )""")
    cursor.execute("PRAGMA table_info(positions)")
    columns = [row[1] for row in cursor.fetchall()]
    
    migrations = {
        'tier': "ALTER TABLE positions ADD COLUMN tier TEXT DEFAULT 'Unknown'",
        'expected_pump': "ALTER TABLE positions ADD COLUMN expected_pump TEXT DEFAULT 'N/A'",
        'funding_rate': "ALTER TABLE positions ADD COLUMN funding_rate REAL DEFAULT 0",
        'oi_change': "ALTER TABLE positions ADD COLUMN oi_change REAL DEFAULT 0",
        'supply_ratio': "ALTER TABLE positions ADD COLUMN supply_ratio REAL DEFAULT 0",
        'top_holders_pct': "ALTER TABLE positions ADD COLUMN top_holders_pct REAL DEFAULT 0",
        'news_sentiment': "ALTER TABLE positions ADD COLUMN news_sentiment TEXT DEFAULT 'neutral'",
        'position_size': "ALTER TABLE positions ADD COLUMN position_size REAL DEFAULT 0",
        'risk_reward': "ALTER TABLE positions ADD COLUMN risk_reward REAL DEFAULT 0"
    }
    for col, sql in migrations.items():
        if col not in columns:
            try:
                cursor.execute(sql)
                conn.commit()
            except Exception as e:
                logger.warning(f"Migration error for {col}: {e}")
    conn.close()

def _save_position_sync(**kwargs):
    conn = sqlite3.connect(DB_PATH)
    columns = ', '.join(kwargs.keys())
    placeholders = ', '.join(['?' for _ in kwargs])
    values = tuple(kwargs.values())
    conn.execute(f"INSERT OR REPLACE INTO positions ({columns}, status) VALUES ({placeholders}, 'open')", values)
    conn.commit()
    conn.close()

async def save_position(**kwargs):
    await asyncio.to_thread(_save_position_sync, **kwargs)

def _get_active_positions_sync() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    positions = [dict(row) for row in conn.execute("SELECT * FROM positions WHERE status = 'open'")]
    conn.close()
    return positions

async def get_active_positions() -> List[Dict]:
    return await asyncio.to_thread(_get_active_positions_sync)

def _update_position_status_sync(coin: str, status: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE positions SET status = ? WHERE coin = ?", (status, coin))
    conn.commit()
    conn.close()

async def update_position_status(coin: str, status: str):
    await asyncio.to_thread(_update_position_status_sync, coin, status)

# ==========================================
# 🔍 جستجوی شبکه‌های اجتماعی
# ==========================================
def search_social_sentiment(coin_name: str) -> List[Dict]:
    influencers = []
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            for q in [f"{coin_name} crypto site:x.com OR site:twitter.com", f"{coin_name} crypto site:t.me"]:
                try:
                    for r in list(ddgs.text(q, max_results=5)):
                        title, body, href = r.get('title', ''), r.get('body', ''), r.get('href', '')
                        if coin_name.lower() in (title + body).lower():
                            username = "ناشناس"
                            if 'x.com/' in href or 'twitter.com/' in href:
                                m = re.search(r'(?:x\.com|twitter\.com)/(\w+)', href)
                                if m: username = m.group(1)
                            elif 't.me/' in href:
                                m = re.search(r't\.me/(\w+)', href)
                                if m: username = m.group(1)
                            if username not in [inf['username'] for inf in influencers]:
                                platform = "توییتر" if 'x.com' in href or 'twitter.com' in href else "تلگرام"
                                influencers.append({"username": username, "platform": platform, "comment": (body or title)[:100], "link": href})
                except Exception as e:
                    if DEBUG: logger.debug(f"Search error: {e}")
    except Exception as e:
        if DEBUG: logger.debug(f"DDGS error: {e}")
    return influencers

# ==========================================
# 📰 تحلیل اخبار
# ==========================================
async def get_news_sentiment(symbol: str, client: httpx.AsyncClient) -> Optional[Dict]:
    if not CRYPTOPANIC_API_KEY:
        return None
    
    try:
        url = f"https://cryptopanic.com/api/v1/posts/?auth_token={CRYPTOPANIC_API_KEY}&currencies={symbol}&kind=news&filter=important"
        response = await client.get(url, timeout=10)
        if response.status_code != 200:
            return None
        
        data = response.json()
        results = data.get('results', [])
        
        if not results:
            return {'sentiment': 'neutral', 'score': 0, 'news_count': 0}
        
        positive_votes = sum(r.get('votes', {}).get('positive', 0) for r in results[:10])
        negative_votes = sum(r.get('votes', {}).get('negative', 0) for r in results[:10])
        
        total_votes = positive_votes + negative_votes
        if total_votes == 0:
            return {'sentiment': 'neutral', 'score': 0, 'news_count': len(results)}
        
        sentiment_score = (positive_votes - negative_votes) / total_votes
        
        if sentiment_score > 0.3:
            sentiment = 'bullish'
        elif sentiment_score < -0.3:
            sentiment = 'bearish'
        else:
            sentiment = 'neutral'
        
        return {
            'sentiment': sentiment,
            'score': round(sentiment_score, 2),
            'news_count': len(results),
            'positive_votes': positive_votes,
            'negative_votes': negative_votes
        }
    except Exception as e:
        logger.error(f"News sentiment error: {e}")
        return None

# ==========================================
# 📊 دریافت قیمت از صرافی‌ها
# ==========================================
async def get_price_from_exchanges_async(symbol: str, client: httpx.AsyncClient) -> Dict:
    symbol_usdt = symbol + 'USDT'
    results = {}
    tasks = [
        ('binance', client.get(f"{EXCHANGES['binance']}/ticker/24hr?symbol={symbol_usdt}")),
        ('okx', client.get(f"{EXCHANGES['okx']}/market/ticker?instId={symbol}-USDT")),
        ('mexc', client.get(f"{EXCHANGES['mexc']}/ticker/24hr?symbol={symbol_usdt}")),
        ('bybit', client.get(f"{EXCHANGES['bybit']}/market/tickers?category=spot&symbol={symbol_usdt}"))
    ]
    responses = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)

    for (name, _), response in zip(tasks, responses):
        try:
            if isinstance(response, Exception) or response.status_code != 200: continue
            data = response.json()
            if name == 'binance':
                results['binance'] = {'price': float(data['lastPrice']), 'change': float(data['priceChangePercent']), 'volume': float(data['quoteVolume'])}
            elif name == 'okx' and data.get('data'):
                d = data['data'][0]
                last, open24h = float(d['last']), float(d['open24h'])
                results['okx'] = {'price': last, 'change': ((last - open24h) / open24h) * 100 if open24h > 0 else 0, 'volume': float(d['volCcy24h']) * last}
            elif name == 'mexc':
                results['mexc'] = {'price': float(data['lastPrice']), 'change': float(data['priceChangePercent']), 'volume': float(data['quoteVolume'])}
            elif name == 'bybit' and data.get('result', {}).get('list'):
                d = data['result']['list'][0]
                results['bybit'] = {'price': float(d['lastPrice']), 'change': float(d['price24hPcnt']) * 100, 'volume': float(d['turnover24h'])}
        except Exception: continue
    return results

# ==========================================
# 📈 محاسبه Z-Score هوشمند
# ==========================================
async def calculate_volume_z_score_smart(symbol: str, client: httpx.AsyncClient, market_cap: float = 0) -> Tuple[float, float, str, str]:
    threshold = get_tier_threshold(market_cap)
    interval = threshold['interval']
    limit = threshold['limit']
    
    try:
        response = await client.get(f"{EXCHANGES['binance']}/klines?symbol={symbol}USDT&interval={interval}&limit={limit}")
        if response.status_code == 200:
            data = response.json()
            min_candles = 20 if interval == '4h' else 24
            if len(data) >= min_candles:
                volumes = np.array([float(k[7]) for k in data[:-1]])
                if len(volumes) >= min_candles:
                    current = float(data[-1][7])
                    mean, std = volumes.mean(), volumes.std(ddof=1)
                    if std > 0:
                        z = (current - mean) / std
                        mult = current / mean if mean > 0 else 0.0
                        return float(z), float(mult), interval, threshold['tier_name']
    except:
        pass
    
    try:
        response = await client.get(f"{EXCHANGES['mexc']}/klines?symbol={symbol}USDT&interval={interval}&limit={limit}")
        if response.status_code == 200:
            data = response.json()
            if data.get('data') and len(data['data']) >= 20:
                volumes = np.array([float(k.get('vol', 0)) for k in data['data'][:-1]])
                if len(volumes) >= 20:
                    current = float(data['data'][-1].get('vol', 0))
                    mean, std = volumes.mean(), volumes.std(ddof=1)
                    if std > 0:
                        z = (current - mean) / std
                        mult = current / mean if mean > 0 else 0.0
                        return float(z), float(mult), interval, threshold['tier_name']
    except:
        pass
    
    return 0.0, 0.0, interval, threshold['tier_name']

# ==========================================
# 📐 محاسبه ATR
# ==========================================
async def get_atr_async(symbol: str, client: httpx.AsyncClient, period: int = 14) -> Optional[float]:
    try:
        response = await client.get(f"{EXCHANGES['binance']}/klines?symbol={symbol}USDT&interval=1h&limit={period+2}")
        if response.status_code != 200: return None
        data = response.json()
        if len(data) < period + 2: return None
        data = data[:-1]
        trs = [max(float(data[i][2]) - float(data[i][3]), abs(float(data[i][2]) - float(data[i-1][4])), abs(float(data[i][3]) - float(data[i-1][4]))) for i in range(1, len(data))]
        return float(np.mean(trs)) if trs else None
    except Exception: return None

# ==========================================
# 😱 حس بازار
# ==========================================
async def get_market_sentiment_async(client: httpx.AsyncClient) -> Optional[Dict]:
    try:
        response = await client.get("https://api.alternative.me/fng/?limit=1")
        if response.status_code != 200: return None
        data = response.json()
        if not data or not data.get('data'): return None
        value = int(data['data'][0]['value'])
        if value <= 25: emoji, status = "😱", "ترس شدید"
        elif value <= 45: emoji, status = "😨", "ترس"
        elif value <= 55: emoji, status = "😐", "خنثی"
        elif value <= 75: emoji, status = "😊", "طمع"
        else: emoji, status = "🤑", "طمع شدید"
        return {'value': value, 'emoji': emoji, 'status': status, 'signal': 'risk_on' if value > 50 else 'risk_off'}
    except Exception: return None

# ==========================================
# 📊 داده‌های مشتقات
# ==========================================
async def get_funding_rate_async(symbol: str, client: httpx.AsyncClient) -> Optional[Dict]:
    try:
        response = await client.get(f"{EXCHANGES['binance_futures']}/fundingRate?symbol={symbol}USDT&limit=10")
        if response.status_code != 200: return None
        data = response.json()
        if not data: return None
        latest = data[-1]
        funding_rate = float(latest['fundingRate'])
        avg_funding = np.mean([float(d['fundingRate']) for d in data])
        return {'current': funding_rate, 'average': avg_funding, 'signal': 'bullish' if funding_rate < 0 else 'bearish' if funding_rate > 0.01 else 'neutral'}
    except Exception: return None

async def get_open_interest_async(symbol: str, client: httpx.AsyncClient) -> Optional[Dict]:
    try:
        response = await client.get(f"{EXCHANGES['binance_futures']}/openInterest?symbol={symbol}USDT")
        if response.status_code != 200: return None
        current_oi = float(response.json()['openInterest'])
        oi_change = 0
        response2 = await client.get(f"{EXCHANGES['binance_futures']}/openInterestHist?symbol={symbol}USDT&period=1h&limit=24")
        if response2.status_code == 200:
            hist_data = response2.json()
            if len(hist_data) >= 2:
                oi_24h_ago = float(hist_data[0]['sumOpenInterest'])
                oi_change = ((current_oi - oi_24h_ago) / oi_24h_ago * 100) if oi_24h_ago > 0 else 0
        signal = 'bullish' if oi_change > 10 else 'bearish' if oi_change < -10 else 'neutral'
        return {'current': current_oi, 'change_24h': oi_change, 'signal': signal}
    except Exception: return None

# ==========================================
# 💰 Supply Data
# ==========================================
async def get_supply_data_async(symbol: str, client: httpx.AsyncClient) -> Optional[Dict]:
    try:
        coin_id = COINGECKO_ID_MAP.get(symbol, symbol.lower())
        if coin_id == symbol.lower():
            search_response = await client.get(f"{COINGECKO_URL}/search?query={symbol}")
            if search_response.status_code == 200:
                search_data = search_response.json()
                coins = search_data.get('coins', [])
                if coins:
                    coin_id = coins[0]['id']
        response = await client.get(f"{COINGECKO_URL}/coins/{coin_id}?localization=false&tickers=false&market_data=true&community_data=false&developer_data=false")
        if response.status_code != 200: return None
        data = response.json()
        market_data = data.get('market_data', {})
        circulating = market_data.get('circulating_supply', 0)
        total = market_data.get('total_supply', 0)
        max_supply = market_data.get('max_supply', 0)
        supply_ratio = (circulating / total) if (circulating > 0 and total > 0) else 1.0
        return {'circulating': circulating, 'total': total, 'max': max_supply, 'ratio': supply_ratio, 'low_float': supply_ratio < 0.5}
    except Exception: return None

# ==========================================
# 📊 پیش‌بینی درصد پامپ
# ==========================================
def predict_pump_percentage(z_score: float, vol_mult: float, atr_ratio: float, tier_name: str, 
                            funding_rate: float = 0, oi_change: float = 0, news_sentiment: str = 'neutral') -> Dict:
    z_weight, mult_weight, atr_weight = 8, 5, 15
    base_prediction = (z_score * z_weight) + (vol_mult * mult_weight) + (atr_ratio * 100 * atr_weight)
    
    tier_multiplier = {'Mega': 0.5, 'Large': 0.7, 'Mid': 1.0, 'Small': 1.5}.get(tier_name, 1.0)
    adjusted_prediction = base_prediction * tier_multiplier
    
    if funding_rate < -0.01: adjusted_prediction *= 1.2
    elif funding_rate > 0.05: adjusted_prediction *= 0.8
    
    if oi_change > 20: adjusted_prediction *= 1.15
    if news_sentiment == 'bullish': adjusted_prediction *= 1.15
    elif news_sentiment == 'bearish': adjusted_prediction *= 0.85
    
    min_pump = max(5, adjusted_prediction * 0.5)
    likely_pump = adjusted_prediction
    max_pump = min(300, adjusted_prediction * 2.0)
    
    confidence = "بالا" if (z_score >= 2.5 and vol_mult >= 3.0) else ("متوسط" if (z_score >= 1.5 and vol_mult >= 2.0) else "پایین")
    
    return {'min': round(min_pump, 1), 'likely': round(likely_pump, 1), 'max': round(max_pump, 1), 'confidence': confidence}

# ==========================================
# 🎯 تحلیل کامل یک کوین
# ==========================================
async def analyze_coin_full_async(symbol_raw: str) -> Tuple[str, Optional[Dict]]:
    symbol = symbol_raw.upper().strip()
    if symbol.endswith('USDT'): symbol = symbol[:-4]
    time_info = get_time_info()

    async with httpx.AsyncClient(timeout=10) as client:
        exchange_data, sentiment, atr, social_results, funding_data, oi_data, supply_data, news_data = await asyncio.gather(
            get_price_from_exchanges_async(symbol, client),
            get_market_sentiment_async(client),
            get_atr_async(symbol, client),
            asyncio.to_thread(search_social_sentiment, symbol),
            get_funding_rate_async(symbol, client),
            get_open_interest_async(symbol, client),
            get_supply_data_async(symbol, client),
            get_news_sentiment(symbol, client)
        )
    
    if not exchange_data:
        return f"❌ کوین {escape(symbol)} در هیچ صرافی یافت نشد", None

    total_vol = sum(ex['volume'] for ex in exchange_data.values())
    avg_price = sum(ex['price'] * ex['volume'] for ex in exchange_data.values()) / total_vol if total_vol > 0 else np.mean([ex['price'] for ex in exchange_data.values()])
    avg_change = np.mean([ex['change'] for ex in exchange_data.values()])

    estimated_mc = estimate_market_cap(total_vol)
    z_score, vol_mult, interval_used, tier_name = await calculate_volume_z_score_smart(symbol, client, estimated_mc)
    threshold = get_tier_threshold(estimated_mc)
    atr_ratio = (atr / avg_price) if atr and avg_price > 0 else 0.02

    funding_rate = funding_data['current'] if funding_data else 0
    oi_change = oi_data['change_24h'] if oi_data else 0
    supply_ratio = supply_data['ratio'] if supply_data else 1.0
    news_sentiment = news_data['sentiment'] if news_data else 'neutral'

    pump_prediction = predict_pump_percentage(z_score, vol_mult, atr_ratio, tier_name, funding_rate, oi_change, news_sentiment)
    expected_pump_text = f"{pump_prediction['min']}% - {pump_prediction['likely']}% - {pump_prediction['max']}%"

    slippage = threshold['slippage']
    if atr and atr > 0:
        stop_loss = avg_price - 2*atr
        tp1 = avg_price + 1.5*atr
        tp2 = avg_price + 3*atr
        tp3 = avg_price + 5*atr
    else:
        stop_loss = avg_price * (1 - 0.15 - slippage)
        tp1 = avg_price * 1.20
        tp2 = avg_price * 1.40
        tp3 = avg_price * 1.60

    risk_manager = RiskManager(ACCOUNT_BALANCE, RISK_PER_TRADE, MAX_POSITIONS)
    position_size = risk_manager.calculate_position_size(avg_price, stop_loss, z_score)
    risk_reward = risk_manager.calculate_risk_reward(avg_price, stop_loss, tp3)

    score = 0
    z_threshold = threshold['z_threshold']
    if z_score >= z_threshold + 0.5: score += 20
    elif z_score >= z_threshold: score += 15
    elif z_score >= z_threshold - 0.3: score += 10
    if -10 <= avg_change <= 30: score += 10
    if total_vol > threshold['min_vol']: score += 10
    if sentiment and sentiment['value'] > 50: score += 5
    
    if funding_data:
        if funding_data['current'] < -0.01: score += 10
        elif funding_data['current'] < 0.01: score += 7
        elif funding_data['current'] < 0.05: score += 3
    if oi_data:
        if oi_data['change_24h'] > 20: score += 10
        elif oi_data['change_24h'] > 10: score += 7
        elif oi_data['change_24h'] > 0: score += 3
    if supply_data:
        if supply_data['low_float']: score += 8
        elif supply_ratio < 0.7: score += 5
        elif supply_ratio < 0.9: score += 3
    
    if news_data:
        if news_data['sentiment'] == 'bullish': score += 10
        elif news_data['sentiment'] == 'bearish': score -= 5

    if z_score >= z_threshold and -10 <= avg_change <= 30: status = "🟢 سیگنال قوی Pre-Pump"
    elif z_score >= z_threshold - 0.3: status = "🟡 سیگنال متوسط"
    elif avg_change > 50: status = "🔴 قبلاً پامپ کرده"
    else: status = "⚪ عادی"

    report = f"🔍 <b>تحلیل کامل {escape(symbol)}</b>\n⏰ {time_info['iran_time']}\n\n"
    report += f"💰 <b>قیمت:</b> ${avg_price:,.4f} ({avg_change:+.2f}%)\n"
    report += f"📈 <b>Z-Score:</b> {z_score:.2f} | <b>Vol:</b> {vol_mult:.1f}x\n"
    report += f"🎯 <b>وضعیت:</b> {status}\n\n"
    
    if news_data:
        news_emoji = "🟢" if news_data['sentiment'] == 'bullish' else "🔴" if news_data['sentiment'] == 'bearish' else "⚪"
        report += f"{news_emoji} <b>اخبار:</b> {news_data['sentiment']} (Score: {news_data['score']})\n"
        report += f"   تعداد اخبار: {news_data['news_count']}\n\n"
    
    report += f"💵 <b>مدیریت ریسک:</b>\n"
    report += f"• ورود: ${avg_price:,.4f}\n"
    report += f"• استاپ: ${stop_loss:,.4f}\n"
    report += f"• تارگت 1: ${tp1:,.4f}\n"
    report += f"• تارگت 2: ${tp2:,.4f}\n"
    report += f"• تارگت 3: ${tp3:,.4f}\n"
    report += f"• <b>حجم پیشنهادی:</b> ${position_size:,.2f}\n"
    report += f"• <b>R:R Ratio:</b> 1:{risk_reward:.2f}\n\n"
    
    report += f"🚀 <b>پیش‌بینی پامپ:</b> {expected_pump_text}\n"
    report += f"🎯 <b>امتیاز:</b> {score}/100\n\n"
    
    if score >= 75: report += f"✅ <b>توصیه:</b> سیگنال قوی\n"
    elif score >= 60: report += f"⚠️ <b>توصیه:</b> سیگنال متوسط\n"
    else: report += f"❌ <b>توصیه:</b> سیگنال ضعیف\n"

    position_data = {
        'coin': symbol, 'entry_price': avg_price, 'stop_loss': stop_loss, 
        'take_profit_1': tp1, 'take_profit_2': tp2, 'take_profit_3': tp3,
        'z_score': z_score, 'score': score, 'tier': tier_name, 
        'entry_time': time_info['iran_time'], 'expected_pump': expected_pump_text,
        'funding_rate': funding_rate, 'oi_change': oi_change,
        'supply_ratio': supply_ratio, 'top_holders_pct': 0,
        'news_sentiment': news_sentiment, 'position_size': position_size,
        'risk_reward': risk_reward
    }
    return report, position_data

# ==========================================
# ⚡ اسکن سریع
# ==========================================
async def quick_scan_async() -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{EXCHANGES['binance']}/ticker/24hr")
            if response.status_code != 200: return None
            
            df = pd.DataFrame(response.json())
            df['priceChangePercent'] = pd.to_numeric(df['priceChangePercent'])
            df['quoteVolume'] = pd.to_numeric(df['quoteVolume'])
            
            df = df[df['quoteVolume'] > 200_000]
            df = df[(df['priceChangePercent'] >= -30) & (df['priceChangePercent'] <= 100)]
            df = df[df['symbol'].str.endswith('USDT')]
            
            if df.empty: return None

            final = []
            for _, row in df.sort_values(by='quoteVolume', ascending=False).head(30).iterrows():
                symbol = row['symbol'].replace('USDT', '')
                estimated_mc = estimate_market_cap(row['quoteVolume'])
                threshold = get_tier_threshold(estimated_mc)
                
                z, mult, interval_used, tier_name = await calculate_volume_z_score_smart(symbol, client, estimated_mc)
                
                relaxed_z = threshold['z_threshold'] * 0.5
                relaxed_mult = threshold['vol_mult'] * 0.5
                
                if z >= relaxed_z or mult >= relaxed_mult:
                    try:
                        funding_resp = await client.get(f"{EXCHANGES['binance_futures']}/fundingRate?symbol={symbol}USDT&limit=1")
                        funding_data = funding_resp.json()
                        funding_rate = float(funding_data[0]['fundingRate']) if funding_data else 0
                    except: 
                        funding_rate = 0
                    
                    pump_pred = predict_pump_percentage(z, mult, 0.02, tier_name, funding_rate, 0)
                    final.append({
                        'symbol': symbol, 'change': row['priceChangePercent'], 'z': round(z, 2), 
                        'mult': round(mult, 2), 'tier': tier_name, 
                        'expected_pump': f"{pump_pred['likely']}%", 'funding': funding_rate
                    })

            if not final: return None
            
            final.sort(key=lambda x: x['z'], reverse=True)
            top10 = final[:10]
            
            report = f"⚡ <b>اسکن سریع</b>\n⏰ {get_time_info()['iran_time']}\n\n🏆 <b>کاندیداها:</b>\n"
            for c in top10:
                report += f"• <b>{c['symbol']}</b> [{c['tier']}] | Z: {c['z']:.2f} | Pump: {c['expected_pump']}\n"
            return report
    except Exception as e:
        logger.error(f"Scan error: {e}")
        return None

# ==========================================
# 🔄 بررسی خروج
# ==========================================
async def check_position_exit_async(position: Dict) -> Tuple[Optional[str], str]:
    coin = position['coin']
    entry_price = position['entry_price']
    stop_loss = position['stop_loss']
    tp3 = position['take_profit_3']
    async with httpx.AsyncClient(timeout=10) as client:
        exchange_data = await get_price_from_exchanges_async(coin, client)
    if not exchange_data: return None, "HOLD"
    total_vol = sum(ex['volume'] for ex in exchange_data.values())
    current_price = sum(ex['price'] * ex['volume'] for ex in exchange_data.values()) / total_vol if total_vol > 0 else np.mean([ex['price'] for ex in exchange_data.values()])
    reasons = []
    if current_price <= stop_loss: reasons.append(f"🔴 استاپ لاس")
    if current_price >= tp3: reasons.append(f"🟢 تارگت 3")
    if reasons:
        exit_msg = f"🚨 <b>خروج از {escape(coin)}</b>\n• ورود: ${entry_price:,.4f}\n• فعلی: ${current_price:,.4f}\n• سود: {((current_price - entry_price) / entry_price * 100):+.2f}%\n\n" + "\n".join(reasons)
        return exit_msg, "EXIT"
    return None, "HOLD"

# ==========================================
# 🤖 Handlers
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 <b>ربات Pre-Pump v4.0</b>\n\n/scan - اسکن\n/positions - پوزیشن‌ها\n/backtest - اجرای بک‌تست\n\nنام کوین را بفرستید", parse_mode='HTML')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ دسترسی غیرمجاز")
        return
    if not rate_limited(user_id, 30):
        await update.message.reply_text("⏳ 30 ثانیه صبر کنید")
        return
    text = update.message.text.strip().upper()
    if text.startswith('/'): return
    if len(text) >= 2 and len(text) <= 10 and text.isalpha():
        await update.message.reply_text(f"⏳ تحلیل <b>{escape(text)}</b>...", parse_mode='HTML')
        try:
            report, position_data = await analyze_coin_full_async(text)
            if position_data and position_data['score'] >= 70:
                await save_position(**position_data)
                report += "\n\n✅ پوزیشن ذخیره شد."
            for chunk in [report[i:i+4000] for i in range(0, len(report), 4000)]:
                await update.message.reply_text(chunk, parse_mode='HTML', disable_web_page_preview=True)
        except Exception as e:
            await update.message.reply_text(f"❌ خطا: {str(e)}")

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS: return
    await update.message.reply_text("⚡ اسکن...")
    report = await quick_scan_async()
    await update.message.reply_text(report or "✅ سیگنالی یافت نشد", parse_mode='HTML')

async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS: return
    positions = await get_active_positions()
    if not positions:
        await update.message.reply_text("📭 پوزیشنی ندارید")
        return
    report = "📊 <b>پوزیشن‌ها:</b>\n\n"
    for p in positions:
        report += f"<b>{escape(p['coin'])}</b>\n• ورود: ${p['entry_price']:,.4f}\n• استاپ: ${p['stop_loss']:,.4f}\n• حجم: ${p.get('position_size', 0):,.2f}\n\n"
    await update.message.reply_text(report, parse_mode='HTML')

async def backtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS: return
    await update.message.reply_text("🧪 در حال اجرای بک‌تست... (2-3 دقیقه)")
    try:
        from backtest import run_backtest
        result = await asyncio.to_thread(run_backtest)
        await update.message.reply_text(result, parse_mode='HTML')
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در بک‌تست: {str(e)}")

async def scheduled_quick_scan(context: ContextTypes.DEFAULT_TYPE):
    logger.info("⚡ اسکن سریع...")
    report = await quick_scan_async()
    if report:
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=report, parse_mode='HTML')
        except Exception as e: logger.error(f"Send error: {e}")

async def scheduled_full_check(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔄 بررسی پوزیشن‌ها...")
    positions = await get_active_positions()
    for pos in positions:
        exit_msg, status = await check_position_exit_async(pos)
        if status == "EXIT" and exit_msg:
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=exit_msg, parse_mode='HTML')
            except Exception as e: logger.error(f"Exit send error: {e}")
            await update_position_status(pos['coin'], 'closed')

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Bot error: {context.error}")

# ==========================================
# 🚀 اجرای اصلی (اصلاح نهایی - بدون تداخل Event Loop)
# ==========================================
def main():
    # اجرای init_db به صورت sync
    _init_db_sync()
    
    # اجرای Flask Health Check در thread جداگانه (daemon=True برای جلوگیری از تداخل)
    try:
        health_app = Flask(__name__)
        
        @health_app.route('/')
        def health():
            return "Bot is running! 🤖"
        
        def run_health():
            health_app.run(host='0.0.0.0', port=10000, use_reloader=False)
        
        health_thread = threading.Thread(target=run_health, daemon=True)
        health_thread.start()
        logger.info("✅ Health check server started on port 10000")
    except Exception as e:
        logger.warning(f"⚠️ Health check failed to start: {e}")
    
    logger.info("🚀 راه‌اندازی ربات v4.0...")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("scan", scan_command))
    application.add_handler(CommandHandler("positions", positions_command))
    application.add_handler(CommandHandler("backtest", backtest_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    job_queue = application.job_queue
    job_queue.run_repeating(scheduled_quick_scan, interval=SCAN_INTERVAL_MINUTES * 60, first=30)
    job_queue.run_repeating(scheduled_full_check, interval=FULL_ANALYSIS_INTERVAL_MINUTES * 60, first=60)
    
    logger.info("✅ ربات آماده است.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()