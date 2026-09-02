import os
import re
import sqlite3
import asyncio
import time
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from html import escape
from collections import defaultdict
from typing import Optional, Dict, List, Tuple

import httpx
import pandas as pd
import numpy as np
import jdatetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

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

if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ TELEGRAM_TOKEN در فایل .env تنظیم نشده!")

SCAN_INTERVAL_MINUTES = 10
FULL_ANALYSIS_INTERVAL_MINUTES = 60
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "bot.db"

EXCHANGES = {
    'binance': 'https://api.binance.com/api/v3',
    'binance_futures': 'https://fapi.binance.com/fapi/v1',
    'okx': 'https://www.okx.com/api/v5',
    'mexc': 'https://api.mexc.com/api/v3',
    'bybit': 'https://api.bybit.com/v5'
}

COINGECKO_URL = 'https://api.coingecko.com/api/v3'

# ==========================================
# 🎯 سیستم Tier-Based هوشمند
# ==========================================
def get_tier_threshold(market_cap: float) -> Dict:
    if market_cap > 10_000_000_000:
        return {'tier_name': 'Mega', 'z_threshold': 1.3, 'vol_mult': 1.5, 'interval': '4h', 'limit': 42, 'min_vol': 100_000_000}
    elif market_cap > 1_000_000_000:
        return {'tier_name': 'Large', 'z_threshold': 1.5, 'vol_mult': 2.0, 'interval': '4h', 'limit': 42, 'min_vol': 20_000_000}
    elif market_cap > 100_000_000:
        return {'tier_name': 'Mid', 'z_threshold': 2.0, 'vol_mult': 3.0, 'interval': '1h', 'limit': 168, 'min_vol': 5_000_000}
    else:
        return {'tier_name': 'Small', 'z_threshold': 2.5, 'vol_mult': 5.0, 'interval': '1h', 'limit': 168, 'min_vol': 500_000}

def estimate_market_cap(quote_volume_24h: float) -> float:
    return quote_volume_24h * 20

# ==========================================
# 📊 پیش‌بینی درصد پامپ
# ==========================================
def predict_pump_percentage(z_score: float, vol_mult: float, atr_ratio: float, tier_name: str, 
                            funding_rate: float = 0, oi_change: float = 0) -> Dict:
    z_weight, mult_weight, atr_weight = 8, 5, 15
    base_prediction = (z_score * z_weight) + (vol_mult * mult_weight) + (atr_ratio * 100 * atr_weight)
    
    tier_multiplier = {'Mega': 0.5, 'Large': 0.7, 'Mid': 1.0, 'Small': 1.5}.get(tier_name, 1.0)
    adjusted_prediction = base_prediction * tier_multiplier
    
    if funding_rate < -0.01: adjusted_prediction *= 1.2
    elif funding_rate > 0.05: adjusted_prediction *= 0.8
    
    if oi_change > 20: adjusted_prediction *= 1.15
    
    min_pump = max(5, adjusted_prediction * 0.5)
    likely_pump = adjusted_prediction
    max_pump = min(300, adjusted_prediction * 2.0)
    
    confidence = "بالا" if (z_score >= 2.5 and vol_mult >= 3.0) else ("متوسط" if (z_score >= 1.5 and vol_mult >= 2.0) else "پایین")
    
    return {'min': round(min_pump, 1), 'likely': round(likely_pump, 1), 'max': round(max_pump, 1), 'confidence': confidence}

# ==========================================
# 🕐 توابع کمکی زمان
# ==========================================
def get_time_info() -> Dict[str, str]:
    now_utc = datetime.now(timezone.utc)
    iran_tz = timezone(timedelta(hours=3, minutes=30))
    now_iran = now_utc.astimezone(iran_tz)
    now_hijri = jdatetime.datetime.fromgregorian(datetime=now_utc)
    return {
        "iran_time": now_iran.strftime("%Y-%m-%d %H:%M"),
        "hijri_time": now_hijri.strftime("%Y/%m/%d %H:%M"),
        "utc_time": now_utc.strftime("%Y-%m-%d %H:%M UTC")
    }

# ==========================================
# ️ Rate Limiting
# ==========================================
_user_last_call: Dict[int, float] = defaultdict(float)
def rate_limited(user_id: int, min_interval: int = 30) -> bool:
    now = time.time()
    if now - _user_last_call[user_id] < min_interval: return False
    _user_last_call[user_id] = now
    return True

# ==========================================
# 🗄️ دیتابیس SQLite (با Migration خودکار)
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT UNIQUE, entry_price REAL, stop_loss REAL,
        take_profit_1 REAL, take_profit_2 REAL, take_profit_3 REAL, z_score REAL, score INTEGER,
        entry_time TEXT, status TEXT DEFAULT 'open'
    )""")
    cursor.execute("PRAGMA table_info(positions)")
    columns = [row[1] for row in cursor.fetchall()]
    
    migrations = {
        'tier': "ALTER TABLE positions ADD COLUMN tier TEXT DEFAULT 'Unknown'",
        'expected_pump': "ALTER TABLE positions ADD COLUMN expected_pump TEXT DEFAULT 'N/A'",
        'funding_rate': "ALTER TABLE positions ADD COLUMN funding_rate REAL DEFAULT 0",
        'oi_change': "ALTER TABLE positions ADD COLUMN oi_change REAL DEFAULT 0",
        'supply_ratio': "ALTER TABLE positions ADD COLUMN supply_ratio REAL DEFAULT 0",
        'top_holders_pct': "ALTER TABLE positions ADD COLUMN top_holders_pct REAL DEFAULT 0"
    }
    for col, sql in migrations.items():
        if col not in columns:
            print(f"🔧 اضافه کردن ستون {col} به دیتابیس...")
            try:
                cursor.execute(sql)
                conn.commit()
                print(f"✅ ستون {col} اضافه شد.")
            except Exception as e:
                print(f"⚠️ خطا در اضافه کردن {col}: {e}")
    conn.close()

def save_position(coin: str, entry_price: float, stop_loss: float, take_profit_1: float, take_profit_2: float, 
                  take_profit_3: float, z_score: float, score: int, tier: str, entry_time: str, 
                  expected_pump: str, funding_rate: float, oi_change: float, supply_ratio: float, top_holders_pct: float):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""INSERT OR REPLACE INTO positions
        (coin, entry_price, stop_loss, take_profit_1, take_profit_2, take_profit_3,
         z_score, score, tier, entry_time, expected_pump, funding_rate, oi_change, supply_ratio, top_holders_pct, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
        (coin, entry_price, stop_loss, take_profit_1, take_profit_2, take_profit_3,
         z_score, score, tier, entry_time, expected_pump, funding_rate, oi_change, supply_ratio, top_holders_pct))
    conn.commit()
    conn.close()

def get_active_positions() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    positions = [dict(row) for row in conn.execute("SELECT * FROM positions WHERE status = 'open'")]
    conn.close()
    return positions

def update_position_status(coin: str, status: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE positions SET status = ? WHERE coin = ?", (status, coin))
    conn.commit()
    conn.close()

# ==========================================
#  جستجوی شبکه‌های اجتماعی
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
                    if DEBUG: print(f"⚠️ خطا در جستجو: {e}")
    except Exception as e:
        if DEBUG: print(f"⚠️ خطا در DDGS: {e}")
    return influencers

# ==========================================
# 📊 دریافت قیمت از صرافی‌ها (Async)
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
# 📈 محاسبه Z-Score هوشمند (Tier-Based)
# ==========================================
async def calculate_volume_z_score_smart(symbol: str, client: httpx.AsyncClient, market_cap: float = 0) -> Tuple[float, float, str, str]:
    threshold = get_tier_threshold(market_cap)
    try:
        response = await client.get(f"{EXCHANGES['binance']}/klines?symbol={symbol}USDT&interval={threshold['interval']}&limit={threshold['limit']}")
        if response.status_code != 200: return 0.0, 0.0, threshold['interval'], threshold['tier_name']
        data = response.json()
        min_candles = 20 if threshold['interval'] == '4h' else 24
        if len(data) < min_candles: return 0.0, 0.0, threshold['interval'], threshold['tier_name']
        volumes = np.array([float(k[7]) for k in data])
        closed, current = volumes[:-1], volumes[-1]
        mean, std = closed.mean(), closed.std(ddof=1)
        if std == 0: return 0.0, 0.0, threshold['interval'], threshold['tier_name']
        return float((current - mean) / std), float(current / mean if mean > 0 else 0.0), threshold['interval'], threshold['tier_name']
    except Exception: return 0.0, 0.0, '1h', 'Unknown'

# ==========================================
# 📐 محاسبه ATR (Async)
# ==========================================
async def get_atr_async(symbol: str, client: httpx.AsyncClient, period: int = 14) -> Optional[float]:
    try:
        response = await client.get(f"{EXCHANGES['binance']}/klines?symbol={symbol}USDT&interval=1h&limit={period+1}")
        if response.status_code != 200: return None
        data = response.json()
        if len(data) < period + 1: return None
        trs = [max(float(data[i][2]) - float(data[i][3]), abs(float(data[i][2]) - float(data[i-1][4])), abs(float(data[i][3]) - float(data[i-1][4]))) for i in range(1, len(data))]
        return float(np.mean(trs))
    except Exception: return None

# ==========================================
#  حس بازار (Fear & Greed)
# ==========================================
async def get_market_sentiment_async(client: httpx.AsyncClient) -> Optional[Dict]:
    try:
        response = await client.get("https://api.alternative.me/fng/?limit=1")
        if response.status_code != 200: return None
        data = response.json()
        if not data or not data.get('data'): return None
        value = int(data['data'][0]['value'])
        if value <= 25: emoji, status = "😱", "ترس شدید"
        elif value <= 45: emoji, status = "", "ترس"
        elif value <= 55: emoji, status = "😐", "خنثی"
        elif value <= 75: emoji, status = "😊", "طمع"
        else: emoji, status = "🤑", "طمع شدید"
        return {'value': value, 'emoji': emoji, 'status': status, 'signal': 'risk_on' if value > 50 else 'risk_off'}
    except Exception: return None

# ==========================================
# 📊 داده‌های مشتقات (Funding Rate + OI) - فاز 1
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

# ✅ اصلاح باگ: اضافه کردن کلید 'signal' در تمام حالت‌های بازگشتی
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
# 🔗 Holder Distribution (Etherscan) - فاز 1
# ==========================================
def get_holder_distribution(coin_name: str) -> Optional[Dict]:
    if not ETHERSCAN_API_KEY: return None
    contract_map = {
        'ETH': ('0x0000000000000000000000000000000000000000', 'eth'),
        'USDT': ('0xdac17f958d2ee523a2206206994597c13d831ec7', 'eth'),
        'USDC': ('0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', 'eth'),
        'LINK': ('0x514910771af9ca656af840dff83e8264ecf986ca', 'eth'),
        'UNI': ('0x1f9840a85d5af5bf1d1762f925bdaddc4201f984', 'eth'),
        'AAVE': ('0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9', 'eth'),
    }
    if coin_name not in contract_map: return None
    contract_address, chain = contract_map[coin_name]
    api_key = ETHERSCAN_API_KEY if chain == 'eth' else BSCSCAN_API_KEY
    base_url = 'https://api.etherscan.io/api' if chain == 'eth' else 'https://api.bscscan.com/api'
    try:
        response = requests.get(f"{base_url}?module=token&holderlist&contractaddress={contract_address}&page=1&offset=10&apikey={api_key}", timeout=10)
        if response.status_code != 200: return None
        data = response.json()
        if data.get('status') != '1': return None
        holders = data.get('result', [])
        if not holders: return None
        total_supply_estimate = sum(float(h.get('TokenHolderQuantity', 0)) for h in holders)
        return {'top_10_holders': len(holders), 'concentration_risk': 'high' if total_supply_estimate > 1e18 else 'medium' if total_supply_estimate > 1e17 else 'low', 'data_available': True}
    except Exception: return None

# ==========================================
# 💰 Supply Data (CoinGecko) - فاز 1
# ==========================================
async def get_supply_data_async(symbol: str, client: httpx.AsyncClient) -> Optional[Dict]:
    try:
        coin_id = symbol.lower()
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
# 🎯 تحلیل کامل یک کوین (با تمام قابلیت‌های فاز 1)
# ==========================================
async def analyze_coin_full_async(symbol_raw: str) -> Tuple[str, Optional[Dict]]:
    symbol = symbol_raw.upper().strip()
    if symbol.endswith('USDT'): symbol = symbol[:-4]
    time_info = get_time_info()

    async with httpx.AsyncClient(timeout=10) as client:
        exchange_data, sentiment, atr, social_results, funding_data, oi_data, supply_data = await asyncio.gather(
            get_price_from_exchanges_async(symbol, client),
            get_market_sentiment_async(client),
            get_atr_async(symbol, client),
            asyncio.to_thread(search_social_sentiment, symbol),
            get_funding_rate_async(symbol, client),
            get_open_interest_async(symbol, client),
            get_supply_data_async(symbol, client)
        )
    
    holder_data = await asyncio.to_thread(get_holder_distribution, symbol)
    
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
    top_holders_pct = 0

    pump_prediction = predict_pump_percentage(z_score, vol_mult, atr_ratio, tier_name, funding_rate, oi_change)
    expected_pump_text = f"{pump_prediction['min']}% - {pump_prediction['likely']}% - {pump_prediction['max']}%"

    if atr and atr > 0:
        stop_loss, tp1, tp2, tp3 = avg_price - 2*atr, avg_price + 1.5*atr, avg_price + 3*atr, avg_price + 5*atr
    else:
        stop_loss, tp1, tp2, tp3 = avg_price * 0.85, avg_price * 1.20, avg_price * 1.40, avg_price * 1.60

    # امتیازدهی جامع (100 امتیاز)
    score = 0
    z_threshold = threshold['z_threshold']
    if z_score >= z_threshold + 0.5: score += 20
    elif z_score >= z_threshold: score += 15
    elif z_score >= z_threshold - 0.3: score += 10
    if -10 <= avg_change <= 30: score += 10
    if total_vol > threshold['min_vol']: score += 10
    if sentiment and sentiment['value'] > 50: score += 10
    
    if funding_data:
        if funding_data['current'] < -0.01: score += 15
        elif funding_data['current'] < 0.01: score += 10
        elif funding_data['current'] < 0.05: score += 5
    if oi_data:
        if oi_data['change_24h'] > 20: score += 15
        elif oi_data['change_24h'] > 10: score += 10
        elif oi_data['change_24h'] > 0: score += 5
    if supply_data:
        if supply_data['low_float']: score += 10
        elif supply_ratio < 0.7: score += 7
        elif supply_ratio < 0.9: score += 4
    if holder_data:
        if holder_data['concentration_risk'] == 'low': score += 10
        elif holder_data['concentration_risk'] == 'medium': score += 5

    if z_score >= z_threshold and -10 <= avg_change <= 30: status = " سیگنال قوی Pre-Pump"
    elif z_score >= z_threshold - 0.3: status = "🟡 سیگنال متوسط"
    elif avg_change > 50: status = "🔴 قبلاً پامپ کرده"
    else: status = "⚪ عادی"

    report = f"🔍 <b>تحلیل کامل {escape(symbol)}</b>\n⏰ {time_info['iran_time']} | 📅 {time_info['hijri_time']}\n\n"
    report += f"📋 <b>مشخصات کوین:</b>\n• Tier: <b>{tier_name}</b> (MC تخمینی: ${estimated_mc/1e9:.2f}B)\n• Interval: {interval_used} | Z-Threshold: {z_threshold}\n\n"
    report += f"💰 <b>قیمت در صرافی‌ها:</b>\n"
    for ex_name, ex_data in exchange_data.items():
        report += f"• {ex_name.capitalize()}: ${ex_data['price']:,.4f} ({ex_data['change']:+.2f}%)\n"
    report += f"• <b>میانگین وزن‌دار: ${avg_price:,.4f} ({avg_change:+.2f}%)</b>\n• حجم کل 24h: ${total_vol:,.0f}\n\n"
    report += f"📈 <b>تحلیل حجم (Tier-Based):</b>\n• Z-Score: <b>{z_score:.2f}</b> (آستانه: {z_threshold})\n• ضریب جهش: <b>{vol_mult:.1f}x</b> (آستانه: {threshold['vol_mult']}x)\n• وضعیت: <b>{status}</b>\n\n"
    
    report += f"📊 <b>داده‌های مشتقات (Binance Futures):</b>\n"
    if funding_data:
        funding_emoji = "🟢" if funding_data['signal'] == 'bullish' else "" if funding_data['signal'] == 'bearish' else "⚪"
        report += f"• {funding_emoji} Funding Rate: <b>{funding_data['current']*100:.4f}%</b> (میانگین: {funding_data['average']*100:.4f}%)\n"
        report += f"  <i>{'Short Squeeze Potential' if funding_data['signal'] == 'bullish' else 'Over-leveraged Longs' if funding_data['signal'] == 'bearish' else 'Neutral'}</i>\n"
    else: report += f"•  Funding Rate: داده در دسترس نیست\n"
    if oi_data:
        oi_emoji = "🟢" if oi_data['signal'] == 'bullish' else "🔴" if oi_data['signal'] == 'bearish' else "⚪"
        report += f"• {oi_emoji} Open Interest Change (24h): <b>{oi_data['change_24h']:+.2f}%</b>\n"
        report += f"  <i>{'Strong Inflow' if oi_data['signal'] == 'bullish' else 'Outflow' if oi_data['signal'] == 'bearish' else 'Stable'}</i>\n"
    else: report += f"•  Open Interest: داده در دسترس نیست\n"
    report += "\n"

    report += f"💰 <b>توکنومیکس و عرضه:</b>\n"
    if supply_data:
        report += f"• عرضه در گردش: <b>{supply_data['circulating']:,.0f}</b>\n• عرضه کل: <b>{supply_data['total']:,.0f}</b>\n"
        if supply_data['max'] > 0: report += f"• حداکثر عرضه: <b>{supply_data['max']:,.0f}</b>\n"
        report += f"• نسبت عرضه: <b>{supply_data['ratio']*100:.1f}%</b>\n• Low Float: <b>{'✅ بله' if supply_data['low_float'] else '❌ خیر'}</b>\n"
    else: report += f"• ⚪ داده عرضه در دسترس نیست\n\n"

    report += f"👥 <b>توزیع هولدرها:</b>\n"
    if holder_data:
        report += f"• ریسک تمرکز: <b>{holder_data['concentration_risk'].upper()}</b>\n• تعداد هولدرهای برتر بررسی‌شده: <b>{holder_data['top_10_holders']}</b>\n\n"
    else: report += f"• ⚪ داده هولدرها در دسترس نیست (نیاز به Etherscan API Key)\n\n"

    report += f"🚀 <b>پیش‌بینی درصد پامپ احتمالی:</b>\n• حداقل: <b>{pump_prediction['min']}%</b>\n• محتمل‌ترین: <b>{pump_prediction['likely']}%</b>\n• حداکثر: <b>{pump_prediction['max']}%</b>\n• سطح اطمینان: <b>{pump_prediction['confidence']}</b>\n<i>(بر اساس Z-Score، Volume، ATR، Tier، Funding Rate و OI)</i>\n\n"
    
    if sentiment:
        report += f"😱 <b>حس بازار:</b>\n• {sentiment['emoji']} {sentiment['status']} ({sentiment['value']}/100)\n• سیگنال کلان: {'صعودی' if sentiment['signal'] == 'risk_on' else 'نزولی'}\n\n"

    report += f"🎯 <b>نقاط کلیدی (مبتنی بر ATR):</b>\n• <b>نقطه ورود:</b> ${avg_price:,.4f}\n• <b>استاپ لاس:</b> ${stop_loss:,.4f} (2×ATR)\n• <b>تارگت 1:</b> ${tp1:,.4f} (1.5×ATR)\n• <b>تارگت 2:</b> ${tp2:,.4f} (3×ATR)\n• <b>تارگت 3:</b> ${tp3:,.4f} (5×ATR)\n\n"
    report += f"🎯 <b>امتیاز کلی: {score}/100</b>\n\n"
    
    if score >= 75: report += f"✅ <b>توصیه:</b> سیگنال قوی Pre-Pump. ورود در ${avg_price:,.4f}\n"
    elif score >= 60: report += f"⚠️ <b>توصیه:</b> سیگنال متوسط. با احتیاط وارد شوید.\n"
    else: report += f"❌ <b>توصیه:</b> سیگنال ضعیف. ورود توصیه نمی‌شود.\n\n"

    if social_results:
        report += f"💬 <b>بازتاب‌ها در شبکه‌های اجتماعی:</b>\n"
        for i, s in enumerate(social_results[:4], 1):
            emoji = "🐦" if s['platform'] == "توییتر" else "✈️"
            report += f"{i}. {emoji} <b>{s['username']}</b> ({s['platform']})\n   <i>{s['comment']}</i>\n   🔗 <a href='{s['link']}'>مشاهده منبع</a>\n\n"
    else: report += f"💬 <b>بازتاب‌ها در شبکه‌های اجتماعی:</b>\n<i>مورد خاصی برای این ارز یافت نشد.</i>\n"

    report += f"\n⚠️ <i>تحلیل کمی، نه توصیه مالی. ریسک با خودتان است.</i>"

    position_data = {
        'coin': symbol, 'entry_price': avg_price, 'stop_loss': stop_loss, 'take_profit_1': tp1, 'take_profit_2': tp2, 'take_profit_3': tp3,
        'z_score': z_score, 'score': score, 'tier': tier_name, 'entry_time': time_info['iran_time'],
        'expected_pump': expected_pump_text, 'funding_rate': funding_rate, 'oi_change': oi_change,
        'supply_ratio': supply_ratio, 'top_holders_pct': top_holders_pct
    }
    return report, position_data

# ==========================================
# ⚡ اسکن سریع بازار (با داده‌های فاز 1)
# ==========================================
async def quick_scan_async() -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{EXCHANGES['binance']}/ticker/24hr")
            if response.status_code != 200: return None
            df = pd.DataFrame(response.json())
            df['priceChangePercent'] = pd.to_numeric(df['priceChangePercent'])
            df['quoteVolume'] = pd.to_numeric(df['quoteVolume'])
            df = df[df['quoteVolume'] > 1_000_000]
            df = df[(df['priceChangePercent'] >= -15) & (df['priceChangePercent'] <= 40)]
            df = df[df['symbol'].str.endswith('USDT')]
            if df.empty: return None

            final = []
            for _, row in df.sort_values(by='quoteVolume', ascending=False).head(50).iterrows():
                symbol = row['symbol'].replace('USDT', '')
                estimated_mc = estimate_market_cap(row['quoteVolume'])
                threshold = get_tier_threshold(estimated_mc)
                z, mult, interval_used, tier_name = await calculate_volume_z_score_smart(symbol, client, estimated_mc)
                if z >= threshold['z_threshold'] and mult >= threshold['vol_mult']:
                    try:
                        funding_resp = await client.get(f"{EXCHANGES['binance_futures']}/fundingRate?symbol={symbol}USDT&limit=1")
                        funding_data = funding_resp.json()
                        funding_rate = float(funding_data[0]['fundingRate']) if funding_data else 0
                    except: funding_rate = 0
                    pump_pred = predict_pump_percentage(z, mult, 0.02, tier_name, funding_rate, 0)
                    final.append({'symbol': symbol, 'change': row['priceChangePercent'], 'z': round(z, 2), 'mult': round(mult, 2), 'tier': tier_name, 'interval': interval_used, 'threshold': threshold['z_threshold'], 'expected_pump': f"{pump_pred['likely']}%", 'funding': funding_rate})

            if not final: return None
            final.sort(key=lambda x: x['z'], reverse=True)
            top5 = final[:5]
            report = f"⚡ <b>اسکن سریع (هر {SCAN_INTERVAL_MINUTES} دقیقه)</b>\n⏰ {get_time_info()['iran_time']}\n\n🏆 <b>کاندیداهای Pre-Pump:</b>\n"
            for c in top5:
                funding_emoji = "🟢" if c['funding'] < 0 else "🔴" if c['funding'] > 0.01 else "⚪"
                report += f"• <b>{c['symbol']}</b> [{c['tier']}] | Z: {c['z']:.2f} | Vol: {c['mult']:.1f}x | Pump: {c['expected_pump']} | Funding: {funding_emoji}{c['funding']*100:.3f}%\n"
            report += f"\n<i>برای تحلیل کامل، نام کوین را بفرستید (مثلاً {top5[0]['symbol']})</i>"
            return report
    except Exception as e:
        if DEBUG: print(f"❌ خطا در اسکن: {e}")
        return None

# ==========================================
#  بررسی خروج از پوزیشن
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
    if current_price <= stop_loss: reasons.append(f"🔴 قیمت (${current_price:,.4f}) به استاپ لاس (${stop_loss:,.4f}) رسید")
    if current_price >= tp3: reasons.append(f"🟢 قیمت (${current_price:,.4f}) به تارگت 3 (${tp3:,.4f}) رسید - سیو سود")
    estimated_mc = estimate_market_cap(total_vol)
    z_score, _, _, _ = await calculate_volume_z_score_smart(coin, client, estimated_mc)
    if z_score < 1.0: reasons.append(f"️ Z-Score حجم ({z_score:.2f}) زیر 1.0 - ضعف حجم")
    avg_change = np.mean([ex['change'] for ex in exchange_data.values()])
    if avg_change < -20: reasons.append(f"🔴 ریزش شدید ({avg_change:+.2f}%)")
    if reasons:
        exit_msg = f"🚨 <b>سیگنال خروج از {escape(coin)}</b>\n\n• قیمت ورود: ${entry_price:,.4f}\n• قیمت فعلی: ${current_price:,.4f}\n• سود/ضرر: {((current_price - entry_price) / entry_price * 100):+.2f}%\n\n<b>دلایل خروج:</b>\n" + "\n".join([f"• {r}" for r in reasons]) + f"\n\n⏰ {get_time_info()['iran_time']}"
        return exit_msg, "EXIT"
    return None, "HOLD"

# ==========================================
# 🤖 Handlers تلگرام
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 <b>ربات شکارچی Pre-Pump کریپتو (نسخه 2.3 - فاز 1 کامل)</b>

<b>دستورات:</b>
/scan - اسکن سریع بازار
/positions - مشاهده پوزیشن‌های فعال
/help - نمایش این راهنما

<b>بررسی کوین خاص:</b>
فقط نام کوین را بفرستید، مثلاً:
BTC, ETH, SOL, BTR, RNDR, FET

<b>ویژگی‌های نسخه 2.3 (فاز 1):</b>
• فیلتر هوشمند Tier-Based
• پیش‌بینی درصد پامپ
• داده‌های مشتقات (Funding Rate + OI)
• توزیع هولدرها (Etherscan)
• داده‌های Supply (CoinGecko)
• امتیازدهی جامع 100 امتیازی

⏰ اسکن خودکار هر 10 دقیقه فعال است.
"""
    await update.message.reply_text(help_text, parse_mode='HTML')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ دسترسی غیرمجاز")
        return
    if not rate_limited(user_id, 30):
        await update.message.reply_text("⏳ لطفاً 30 ثانیه صبر کنید")
        return
    text = update.message.text.strip().upper()
    if text.startswith('/'): return
    if len(text) >= 2 and len(text) <= 10 and text.isalpha():
        await update.message.reply_text(f" در حال تحلیل <b>{escape(text)}</b> از چندین منبع... (10-15 ثانیه)", parse_mode='HTML')
        try:
            report, position_data = await analyze_coin_full_async(text)
            if position_data and position_data['score'] >= 70:
                save_position(**position_data)
                report += "\n\n✅ پوزیشن ذخیره شد. هر 1 ساعت بررسی خروج انجام می‌شود."
            for chunk in [report[i:i+4000] for i in range(0, len(report), 4000)]:
                await update.message.reply_text(chunk, parse_mode='HTML', disable_web_page_preview=True)
        except Exception as e:
            await update.message.reply_text(f"❌ خطا در تحلیل: {str(e)}")
    else:
        await update.message.reply_text("❓ فقط نام کوین را بفرستید (مثلاً BTC) یا از /help استفاده کنید")

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS: return
    await update.message.reply_text("⚡ در حال اسکن سریع...")
    report = await quick_scan_async()
    await update.message.reply_text(report or "✅ هیچ سیگنال قوی یافت نشد", parse_mode='HTML')

async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS: return
    positions = get_active_positions()
    if not positions:
        await update.message.reply_text("📭 هیچ پوزیشن فعالی ندارید")
        return
    report = "📊 <b>پوزیشن‌های فعال:</b>\n\n"
    for p in positions:
        tier = p.get('tier', 'N/A')
        expected_pump = p.get('expected_pump', 'N/A')
        funding = p.get('funding_rate', 0)
        oi_change = p.get('oi_change', 0)
        report += f"<b>{escape(p['coin'])}</b> [{tier}]\n• ورود: ${p['entry_price']:,.4f}\n• استاپ: ${p['stop_loss']:,.4f}\n• تارگت 3: ${p['take_profit_3']:,.4f}\n• پامپ مورد انتظار: {expected_pump}\n• Funding: {funding*100:.4f}% | OI Change: {oi_change:+.2f}%\n• زمان ورود: {p['entry_time']}\n\n"
    await update.message.reply_text(report, parse_mode='HTML')

async def scheduled_quick_scan(context: ContextTypes.DEFAULT_TYPE):
    print("⚡ اسکن سریع...")
    report = await quick_scan_async()
    if report:
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=report, parse_mode='HTML')
            print("✅ اسکن سریع ارسال شد.")
        except Exception as e: print(f"❌ خطا در ارسال: {e}")

async def scheduled_full_check(context: ContextTypes.DEFAULT_TYPE):
    print("🔄 بررسی پوزیشن‌های فعال...")
    positions = get_active_positions()
    for pos in positions:
        exit_msg, status = await check_position_exit_async(pos)
        if status == "EXIT" and exit_msg:
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=exit_msg, parse_mode='HTML')
                print(f"✅ سیگنال خروج برای {pos['coin']} ارسال شد.")
            except Exception as e: print(f"❌ خطا در ارسال خروج: {e}")
            update_position_status(pos['coin'], 'closed')

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"⚠️ خطا در ربات: {context.error}")

# ==========================================
# 🚀 اجرای اصلی
# ==========================================
def main():
    init_db()
    print("🚀 راه‌اندازی ربات چندمنبعی نسخه 2.3 (فاز 1 کامل)...")
    print(f"⏰ اسکن سریع: هر {SCAN_INTERVAL_MINUTES} دقیقه")
    print(f" بررسی خروج: هر {FULL_ANALYSIS_INTERVAL_MINUTES} دقیقه")
    print(f"👥 کاربران مجاز: {len(ALLOWED_USERS)} نفر")
    print(f"🎯 فیلتر هوشمند: Mega/Large/Mid/Small Tier")
    print(f"🚀 پیش‌بینی درصد پامپ: فعال")
    print(f"📊 داده‌های مشتقات (Funding + OI): فعال")
    print(f" Holder Distribution: فعال (با Etherscan API)")
    print(f"💰 Supply Data: فعال (با CoinGecko)")

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("scan", scan_command))
    application.add_handler(CommandHandler("positions", positions_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    job_queue = application.job_queue
    job_queue.run_repeating(scheduled_quick_scan, interval=SCAN_INTERVAL_MINUTES * 60, first=30)
    job_queue.run_repeating(scheduled_full_check, interval=FULL_ANALYSIS_INTERVAL_MINUTES * 60, first=60)
    print("✅ ربات آماده است.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()