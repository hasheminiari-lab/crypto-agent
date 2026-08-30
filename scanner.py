import os
import json
import time
import re
import sqlite3
import asyncio
import signal
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from html import escape
from functools import lru_cache
from collections import defaultdict
from typing import Optional, Dict, List, Tuple

import httpx
import pandas as pd
import numpy as np
import jdatetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# بارگذاری متغیرهای محیطی
load_dotenv()

# ==========================================
# 🔐 تنظیمات امنیتی (از .env خوانده می‌شود)
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
ALLOWED_USERS = set(map(int, os.getenv("ALLOWED_USERS", "").split(","))) if os.getenv("ALLOWED_USERS") else {ADMIN_CHAT_ID}

# API Keys (اختیاری)
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")

# بررسی TOKEN
if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ TELEGRAM_TOKEN در فایل .env تنظیم نشده!")

# ==========================================
# ⏰ زمان‌بندی
# ==========================================
SCAN_INTERVAL_MINUTES = 10
FULL_ANALYSIS_INTERVAL_MINUTES = 60

# ==========================================
# 📁 مسیرهای فایل
# ==========================================
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "bot.db"

# ==========================================
# 🌐 آدرس APIها
# ==========================================
EXCHANGES = {
    'binance': 'https://api.binance.com/api/v3',
    'okx': 'https://www.okx.com/api/v5',
    'mexc': 'https://api.mexc.com/api/v3',
    'bybit': 'https://api.bybit.com/v5'
}
DEX_SCREENER_URL = 'https://api.dexscreener.com/latest/dex'

# ==========================================
# 🕐 توابع کمکی
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

# Cache برای API calls
_cache: Dict[str, Tuple[any, float]] = {}

def cached_get(url: str, ttl: int = 60) -> Optional[dict]:
    """دریافت داده با کش ۶۰ ثانیه‌ای"""
    now = time.time()
    if url in _cache and now - _cache[url][1] < ttl:
        return _cache[url][0]
    return None

def clear_cache():
    """پاک کردن کش"""
    _cache.clear()

# Rate limiting
_user_last_call: Dict[int, float] = defaultdict(float)

def rate_limited(user_id: int, min_interval: int = 30) -> bool:
    """محدودیت نرخ برای کاربران"""
    now = time.time()
    if now - _user_last_call[user_id] < min_interval:
        return False
    _user_last_call[user_id] = now
    return True

# ==========================================
# 🗄️ دیتابیس SQLite
# ==========================================
def init_db():
    """ایجاد دیتابیس"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coin TEXT UNIQUE,
            entry_price REAL,
            stop_loss REAL,
            take_profit_1 REAL,
            take_profit_2 REAL,
            take_profit_3 REAL,
            z_score REAL,
            score INTEGER,
            entry_time TEXT,
            status TEXT DEFAULT 'open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_position(coin: str, entry_price: float, stop_loss: float, 
                  tp1: float, tp2: float, tp3: float, z_score: float, 
                  score: int, entry_time: str):
    """ذخیره پوزیشن"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO positions 
        (coin, entry_price, stop_loss, take_profit_1, take_profit_2, take_profit_3, 
         z_score, score, entry_time, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
    """, (coin, entry_price, stop_loss, tp1, tp2, tp3, z_score, score, entry_time))
    conn.commit()
    conn.close()

def get_active_positions() -> List[Dict]:
    """دریافت پوزیشن‌های فعال"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM positions WHERE status = 'open'")
    positions = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return positions

def update_position_status(coin: str, status: str):
    """به‌روزرسانی وضعیت پوزیشن"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE positions SET status = ? WHERE coin = ?", (status, coin))
    conn.commit()
    conn.close()

# ==========================================
# 📊 دریافت داده از صرافی‌ها (Async)
# ==========================================
async def get_price_from_exchanges_async(symbol: str, client: httpx.AsyncClient) -> Dict:
    """دریافت قیمت از 4 صرافی به صورت موازی"""
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
            if isinstance(response, Exception):
                continue
            if response.status_code != 200:
                continue
            
            data = response.json()
            
            if name == 'binance':
                results['binance'] = {
                    'price': float(data['lastPrice']),
                    'change': float(data['priceChangePercent']),
                    'volume': float(data['quoteVolume'])
                }
            elif name == 'okx' and data.get('data'):
                d = data['data'][0]
                last = float(d['last'])
                open24h = float(d['open24h'])
                change_24h = ((last - open24h) / open24h) * 100 if open24h > 0 else 0
                results['okx'] = {
                    'price': last,
                    'change': change_24h,
                    'volume': float(d['volCcy24h']) * last
                }
            elif name == 'mexc':
                results['mexc'] = {
                    'price': float(data['lastPrice']),
                    'change': float(data['priceChangePercent']),
                    'volume': float(data['quoteVolume'])
                }
            elif name == 'bybit' and data.get('result', {}).get('list'):
                d = data['result']['list'][0]
                results['bybit'] = {
                    'price': float(d['lastPrice']),
                    'change': float(d['price24hPcnt']) * 100,
                    'volume': float(d['turnover24h'])
                }
        except Exception:
            continue
    
    return results

# ==========================================
# 📈 محاسبه ATR و Z-Score (بهبودیافته)
# ==========================================
async def get_atr_async(symbol: str, client: httpx.AsyncClient, period: int = 14) -> Optional[float]:
    """محاسبه ATR برای استاپ‌لاس داینامیک"""
    try:
        response = await client.get(f"{EXCHANGES['binance']}/klines?symbol={symbol}USDT&interval=1h&limit={period+1}")
        if response.status_code != 200:
            return None
        
        data = response.json()
        if len(data) < period + 1:
            return None
        
        trs = []
        for i in range(1, len(data)):
            high, low = float(data[i][2]), float(data[i][3])
            prev_close = float(data[i-1][4])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
        
        return float(np.mean(trs))
    except:
        return None

async def calculate_volume_z_score_async(symbol: str, client: httpx.AsyncClient) -> Tuple[float, float]:
    """محاسبه Z-Score با کندل‌های ساعتی (دقیق‌تر)"""
    try:
        response = await client.get(f"{EXCHANGES['binance']}/klines?symbol={symbol}USDT&interval=1h&limit=168")
        if response.status_code != 200:
            return 0.0, 0.0
        
        data = response.json()
        if len(data) < 24:
            return 0.0, 0.0
        
        volumes = np.array([float(k[7]) for k in data])
        closed = volumes[:-1]  # فقط کندل‌های بسته‌شده
        current = volumes[-1]
        
        mean, std = closed.mean(), closed.std(ddof=1)
        if std == 0:
            return 0.0, 0.0
        
        z = (current - mean) / std
        multiplier = current / mean if mean > 0 else 0.0
        
        return float(z), float(multiplier)
    except:
        return 0.0, 0.0

# ==========================================
# 🦄 دریافت داده DEX
# ==========================================
async def get_dex_data_async(symbol: str, client: httpx.AsyncClient) -> Optional[Dict]:
    """دریافت داده از DEXScreener با فیلتر دقیق"""
    try:
        response = await client.get(f"{DEX_SCREENER_URL}/search?q={symbol}")
        if response.status_code != 200:
            return None
        
        data = response.json()
        if not data or not data.get('pairs'):
            return None
        
        # فیلتر دقیق: فقط جفت‌هایی که symbol دقیقاً match شود
        pairs = [
            p for p in data['pairs']
            if p.get('baseToken', {}).get('symbol', '').upper() == symbol.upper()
            and p.get('quoteToken', {}).get('symbol', '').upper() in ['USDT', 'USDC', 'WETH', 'WBNB']
        ]
        
        if not pairs:
            return None
        
        # مرتب‌سازی بر اساس حجم
        pairs.sort(key=lambda x: float(x.get('volume', {}).get('h24', 0) or 0), reverse=True)
        best = pairs[0]
        
        return {
            'dex': best.get('dexId', 'unknown'),
            'pair': f"{best.get('baseToken', {}).get('symbol')}/{best.get('quoteToken', {}).get('symbol')}",
            'price': float(best.get('priceUsd', 0)),
            'volume_24h': float(best.get('volume', {}).get('h24', 0) or 0),
            'liquidity': float(best.get('liquidity', {}).get('usd', 0) or 0),
            'change_24h': float(best.get('priceChange', {}).get('h24', 0) or 0)
        }
    except:
        return None

# ==========================================
# ️ Market Sentiment
# ==========================================
async def get_market_sentiment_async(client: httpx.AsyncClient) -> Optional[Dict]:
    """دریافت Fear & Greed Index"""
    try:
        response = await client.get("https://api.alternative.me/fng/?limit=1")
        if response.status_code != 200:
            return None
        
        data = response.json()
        if not data or not data.get('data'):
            return None
        
        value = int(data['data'][0]['value'])
        classification = data['data'][0]['value_classification']
        
        if value <= 25:
            emoji, status = "😱", "ترس شدید"
        elif value <= 45:
            emoji, status = "😨", "ترس"
        elif value <= 55:
            emoji, status = "😐", "خنثی"
        elif value <= 75:
            emoji, status = "😊", "طمع"
        else:
            emoji, status = "🤑", "طمع شدید"
        
        return {
            'value': value,
            'classification': classification,
            'emoji': emoji,
            'status': status,
            'signal': 'risk_on' if value > 50 else 'risk_off'
        }
    except:
        return None

# ==========================================
# 🎯 تحلیل کامل یک کوین (Async)
# ==========================================
async def analyze_coin_full_async(symbol_raw: str) -> Tuple[str, Optional[Dict]]:
    """تحلیل کامل یک کوین با تمام بهبودها"""
    symbol = symbol_raw.upper().strip()
    if symbol.endswith('USDT'):
        symbol = symbol[:-4]
    
    time_info = get_time_info()
    
    async with httpx.AsyncClient(timeout=10) as client:
        # دریافت موازی تمام داده‌ها
        exchange_data, dex_data, (z_score, vol_mult), sentiment, atr = await asyncio.gather(
            get_price_from_exchanges_async(symbol, client),
            get_dex_data_async(symbol, client),
            calculate_volume_z_score_async(symbol, client),
            get_market_sentiment_async(client),
            get_atr_async(symbol, client)
        )
    
    if not exchange_data:
        return f"❌ کوین {escape(symbol)} در هیچ صرافی یافت نشد", None
    
    # میانگین وزن‌دار قیمت (بر اساس حجم)
    total_vol = sum(ex['volume'] for ex in exchange_data.values())
    if total_vol > 0:
        avg_price = sum(ex['price'] * ex['volume'] for ex in exchange_data.values()) / total_vol
    else:
        avg_price = np.mean([ex['price'] for ex in exchange_data.values()])
    
    changes = [ex['change'] for ex in exchange_data.values()]
    avg_change = np.mean(changes)
    
    # محاسبه استاپ و تارگت بر اساس ATR
    if atr and atr > 0:
        stop_loss = avg_price - 2 * atr
        take_profit_1 = avg_price + 1.5 * atr
        take_profit_2 = avg_price + 3 * atr
        take_profit_3 = avg_price + 5 * atr
    else:
        # fallback به درصد ثابت
        stop_loss = avg_price * 0.85
        take_profit_1 = avg_price * 1.20
        take_profit_2 = avg_price * 1.40
        take_profit_3 = avg_price * 1.60
    
    # امتیازدهی
    score = 50
    if z_score >= 2.0: score += 20
    elif z_score >= 1.5: score += 10
    if -10 <= avg_change <= 30: score += 15
    if total_vol > 10_000_000: score += 10
    if dex_data and dex_data['liquidity'] > 250_000: score += 5
    if sentiment and sentiment['value'] > 50: score += 5
    
    # تعیین وضعیت
    if z_score >= 2.0 and -10 <= avg_change <= 30:
        status = "🟢 سیگنال قوی Pre-Pump"
    elif z_score >= 1.5:
        status = "🟡 سیگنال متوسط"
    elif avg_change > 50:
        status = "🔴 قبلاً پامپ کرده"
    else:
        status = " عادی"
    
    # ساخت گزارش
    report = f"🔍 تحلیل کامل {escape(symbol)}\n"
    report += f" {time_info['iran_time']} |  {time_info['hijri_time']}\n\n"
    
    report += f"💰 قیمت در صرافی‌ها:\n"
    for ex_name, ex_data in exchange_data.items():
        report += f"• {ex_name.capitalize()}: ${ex_data['price']:,.4f} ({ex_data['change']:+.2f}%)\n"
    report += f"• <b>میانگین وزن‌دار: ${avg_price:,.4f} ({avg_change:+.2f}%)</b>\n"
    report += f"• حجم کل 24h: ${total_vol:,.0f}\n\n"
    
    if dex_data:
        report += f" داده DEX ({dex_data['dex']}):\n"
        report += f"• جفت: {dex_data['pair']}\n"
        report += f"• قیمت: ${dex_data['price']:,.6f}\n"
        report += f"• نقدینگی: ${dex_data['liquidity']:,.0f}\n"
        report += f"• حجم 24h: ${dex_data['volume_24h']:,.0f}\n\n"
    
    report += f"📊 تحلیل حجم:\n"
    report += f"• Z-Score: <b>{z_score:.2f}</b>\n"
    report += f"• ضریب جهش: <b>{vol_mult:.1f}x</b>\n"
    report += f"• وضعیت: <b>{status}</b>\n\n"
    
    if sentiment:
        report += f"😱 حس بازار:\n"
        report += f"• {sentiment['emoji']} {sentiment['status']} ({sentiment['value']}/100)\n"
        report += f"• سیگنال: {'صعودی' if sentiment['signal'] == 'risk_on' else 'نزولی'}\n\n"
    
    report += f"🎯 نقاط کلیدی (مبتنی بر ATR):\n"
    report += f"• <b>نقطه ورود:</b> ${avg_price:,.4f}\n"
    report += f"• <b>استاپ لاس:</b> ${stop_loss:,.4f} (2×ATR)\n"
    report += f"• <b>تارگت 1:</b> ${take_profit_1:,.4f} (1.5×ATR)\n"
    report += f"• <b>تارگت 2:</b> ${take_profit_2:,.4f} (3×ATR)\n"
    report += f"• <b>تارگت 3:</b> ${take_profit_3:,.4f} (5×ATR)\n\n"
    
    report += f"🎯 امتیاز کلی: <b>{score}/100</b>\n\n"
    
    if score >= 80:
        report += f"✅ توصیه: سیگنال قوی Pre-Pump. ورود در ${avg_price:,.4f}\n"
    elif score >= 60:
        report += f"️ توصیه: سیگنال متوسط. با احتیاط وارد شوید.\n"
    else:
        report += f"❌ توصیه: سیگنال ضعیف. ورود توصیه نمی‌شود.\n\n"
    
    report += f"\n️ تحلیل کمی، نه توصیه مالی. ریسک با خودتان است."
    
    position_data = {
        'coin': symbol,
        'entry_price': avg_price,
        'stop_loss': stop_loss,
        'take_profit_1': take_profit_1,
        'take_profit_2': take_profit_2,
        'take_profit_3': take_profit_3,
        'z_score': z_score,
        'score': score,
        'entry_time': time_info['iran_time']
    }
    
    return report, position_data

# ==========================================
# 🔍 بررسی خروج از پوزیشن
# ==========================================
async def check_position_exit_async(position: Dict) -> Tuple[Optional[str], str]:
    """بررسی شرایط خروج"""
    coin = position['coin']
    entry_price = position['entry_price']
    stop_loss = position['stop_loss']
    tp3 = position['take_profit_3']
    
    async with httpx.AsyncClient(timeout=10) as client:
        exchange_data = await get_price_from_exchanges_async(coin, client)
    
    if not exchange_data:
        return None, "❌ قیمت دریافت نشد"
    
    total_vol = sum(ex['volume'] for ex in exchange_data.values())
    current_price = sum(ex['price'] * ex['volume'] for ex in exchange_data.values()) / total_vol if total_vol > 0 else np.mean([ex['price'] for ex in exchange_data.values()])
    
    reasons = []
    
    if current_price <= stop_loss:
        reasons.append(f"🔴 قیمت (${current_price:,.4f}) به استاپ لاس (${stop_loss:,.4f}) رسید")
    
    if current_price >= tp3:
        reasons.append(f" قیمت (${current_price:,.4f}) به تارگت 3 (${tp3:,.4f}) رسید - سیو سود")
    
    async with httpx.AsyncClient(timeout=10) as client:
        z_score, _ = await calculate_volume_z_score_async(coin, client)
    
    if z_score < 1.0:
        reasons.append(f"⚠️ Z-Score حجم ({z_score:.2f}) زیر 1.0 - ضعف حجم")
    
    changes = [ex['change'] for ex in exchange_data.values()]
    avg_change = np.mean(changes)
    if avg_change < -20:
        reasons.append(f"🔴 ریزش شدید ({avg_change:+.2f}%)")
    
    if reasons:
        exit_msg = f"🚨 سیگنال خروج از {escape(coin)}\n\n"
        exit_msg += f"• قیمت ورود: ${entry_price:,.4f}\n"
        exit_msg += f"• قیمت فعلی: ${current_price:,.4f}\n"
        exit_msg += f"• سود/ضرر: {((current_price - entry_price) / entry_price * 100):+.2f}%\n\n"
        exit_msg += f"<b>دلایل خروج:</b>\n"
        for r in reasons:
            exit_msg += f"• {r}\n"
        exit_msg += f"\n⏰ {get_time_info()['iran_time']}"
        return exit_msg, "EXIT"
    
    return None, "HOLD"

# ==========================================
#  اسکن سریع بازار
# ==========================================
async def quick_scan_async() -> Optional[str]:
    """اسکن سریع بازار"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{EXCHANGES['binance']}/ticker/24hr")
            if response.status_code != 200:
                return None
            
            data = response.json()
            df = pd.DataFrame(data)
            df['priceChangePercent'] = pd.to_numeric(df['priceChangePercent'])
            df['quoteVolume'] = pd.to_numeric(df['quoteVolume'])
            
            df = df[df['quoteVolume'] > 5_000_000]
            df = df[(df['priceChangePercent'] >= -15) & (df['priceChangePercent'] <= 35)]
            df = df[df['symbol'].str.endswith('USDT')]
            
            if df.empty:
                return None
            
            final = []
            top_10 = df.sort_values(by='quoteVolume', ascending=False).head(10)
            
            for _, row in top_10.iterrows():
                symbol = row['symbol'].replace('USDT', '')
                z, mult = await calculate_volume_z_score_async(symbol, client)
                if z >= 2.0:
                    final.append({
                        'symbol': symbol,
                        'change': row['priceChangePercent'],
                        'z': round(z, 2),
                        'mult': round(mult, 2)
                    })
            
            if not final:
                return None
            
            final.sort(key=lambda x: x['z'], reverse=True)
            top3 = final[:3]
            
            time_info = get_time_info()
            report = f"⚡ اسکن سریع (هر 10 دقیقه)\n"
            report += f"⏰ {time_info['iran_time']}\n\n"
            report += " کاندیداهای Pre-Pump:\n"
            report += "| کوین | 24h% | Z-Score | ضریب حجم |\n"
            report += "|---|---|---|---|\n"
            for c in top3:
                report += f"| {c['symbol']} | {c['change']:.2f}% | {c['z']:.2f} | {c['mult']:.1f}x |\n"
            report += f"\n<i>برای تحلیل کامل، نام کوین را بفرستید (مثلاً {top3[0]['symbol']})</i>"
            
            return report
    except:
        return None

# ==========================================
# 🔄 بررسی پوزیشن‌های فعال
# ==========================================
async def check_active_positions_async() -> Optional[str]:
    """بررسی پوزیشن‌های فعال"""
    positions = get_active_positions()
    
    if not positions:
        return None
    
    exit_reports = []
    
    for pos in positions:
        exit_msg, status = await check_position_exit_async(pos)
        if status == "EXIT" and exit_msg:
            exit_reports.append(exit_msg)
            update_position_status(pos['coin'], 'closed')
    
    if exit_reports:
        return "\n\n".join(exit_reports)
    return None

# ==========================================
# 🤖 Handlers تلگرام
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 ربات شکارچی Pre-Pump کریپتو (نسخه 2.0)

دستورات:
/scan - اسکن سریع بازار
/positions - مشاهده پوزیشن‌های فعال
/help - نمایش این راهنما

بررسی کوین خاص:
فقط نام کوین را بفرستید، مثلاً:
BTC, ETH, SOL, DOGE, PEPE

ویژگی‌های نسخه 2.0:
• Async API calls (سرعت 10× بیشتر)
• استاپ‌لاس داینامیک (ATR-based)
• میانگین وزن‌دار قیمت
• دیتابیس SQLite
• کش 60 ثانیه‌ای
• Rate limiting

⏰ اسکن خودکار فعال است.
"""
    await update.message.reply_text(help_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ دسترسی غیرمجاز")
        return
    
    if not rate_limited(user_id, 30):
        await update.message.reply_text(" لطفاً 30 ثانیه صبر کنید")
        return
    
    text = update.message.text.strip().upper()
    
    if text.startswith('/'):
        return
    
    if len(text) >= 2 and len(text) <= 10 and text.isalpha():
        await update.message.reply_text(f"⏳ در حال تحلیل <b>{escape(text)}</b>... (5-10 ثانیه)", parse_mode='HTML')
        
        try:
            report, position_data = await analyze_coin_full_async(text)
            
            if position_data and position_data['score'] >= 70:
                save_position(
                    position_data['coin'],
                    position_data['entry_price'],
                    position_data['stop_loss'],
                    position_data['take_profit_1'],
                    position_data['take_profit_2'],
                    position_data['take_profit_3'],
                    position_data['z_score'],
                    position_data['score'],
                    position_data['entry_time']
                )
                report += "\n\n✅ پوزیشن ذخیره شد. هر 1 ساعت بررسی خروج انجام می‌شود."
            
            # تقسیم پیام اگر طولانی باشد
            chunks = [report[i:i+4000] for i in range(0, len(report), 4000)]
            for chunk in chunks:
                await update.message.reply_text(chunk, parse_mode='HTML', disable_web_page_preview=True)
        except Exception as e:
            await update.message.reply_text(f"❌ خطا در تحلیل: {str(e)}")
    else:
        await update.message.reply_text("❓ فقط نام کوین را بفرستید (مثلاً BTC) یا از /help استفاده کنید")

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text(" دسترسی غیرمجاز")
        return
    
    await update.message.reply_text("⚡ در حال اسکن سریع...")
    report = await quick_scan_async()
    if report:
        await update.message.reply_text(report, parse_mode='HTML')
    else:
        await update.message.reply_text("✅ هیچ سیگنال قوی یافت نشد")

async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ دسترسی غیرمجاز")
        return
    
    positions = get_active_positions()
    
    if not positions:
        await update.message.reply_text("📭 هیچ پوزیشن فعالی ندارید")
        return
    
    report = "📊 پوزیشن‌های فعال:\n\n"
    for p in positions:
        report += f"<b>{escape(p['coin'])}</b>\n"
        report += f"• ورود: ${p['entry_price']:,.4f}\n"
        report += f"• استاپ: ${p['stop_loss']:,.4f}\n"
        report += f"• تارگت 3: ${p['take_profit_3']:,.4f}\n"
        report += f"• زمان ورود: {p['entry_time']}\n\n"
    
    await update.message.reply_text(report, parse_mode='HTML')

async def scheduled_quick_scan(context: ContextTypes.DEFAULT_TYPE):
    print(" اسکن سریع...")
    report = await quick_scan_async()
    if report:
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=report, parse_mode='HTML')
            print("✅ اسکن سریع ارسال شد.")
        except Exception as e:
            print(f"❌ خطا: {e}")

async def scheduled_full_check(context: ContextTypes.DEFAULT_TYPE):
    print("🔄 بررسی پوزیشن‌های فعال...")
    exit_report = await check_active_positions_async()
    if exit_report:
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=exit_report, parse_mode='HTML')
            print("✅ سیگنال خروج ارسال شد.")
        except Exception as e:
            print(f"❌ خطا: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"⚠️ خطا در ربات: {context.error}")

# ==========================================
#  اجرای اصلی
# ==========================================
def main():
    init_db()
    
    print(" راه‌اندازی ربات چندمنبعی نسخه 2.0...")
    print(f"⏰ اسکن سریع: هر {SCAN_INTERVAL_MINUTES} دقیقه")
    print(f"🔄 بررسی خروج: هر {FULL_ANALYSIS_INTERVAL_MINUTES} دقیقه")
    print(f" کاربران مجاز: {len(ALLOWED_USERS)} نفر")
    
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