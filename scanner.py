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

import httpx
import pandas as pd
import numpy as np
import jdatetime
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

try:
    from zoneinfo import ZoneInfo
    TEHRAN_TZ = ZoneInfo("Asia/Tehran")
except ImportError:
    TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))

try:
    from risk_manager import RiskManager
except ImportError:
    class RiskManager:
        def __init__(self, bal, risk, max_pos):
            self.bal, self.risk, self.max_pos = bal, risk, max_pos
        def calculate_position_size(self, entry, stop, z=0, liq=0):
            ra = self.bal * (self.risk / 100)
            ru = abs(entry - stop)
            if ru == 0: return 0
            s = ra / ru * entry
            if liq > 0: s *= min(1.0, liq / 100000)
            return min(s, self.bal * 0.15)
        def calculate_risk_reward(self, entry, stop, target):
            r, w = abs(entry - stop), abs(target - entry)
            return w / r if r > 0 else 0

load_dotenv()

# ==========================================
# ⚙️ تنظیمات
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
ALLOWED_USERS = set(map(int, os.getenv("ALLOWED_USERS", "").split(","))) if os.getenv("ALLOWED_USERS") else {ADMIN_CHAT_ID}
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "10000"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "1.5"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "10"))
SCAN_INTERVAL = 15
CHECK_INTERVAL = 30

if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ TELEGRAM_TOKEN تنظیم نشده!")

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "bot.db"

logger = logging.getLogger("PumpHunter")
logger.setLevel(logging.INFO)
fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
sh = logging.StreamHandler(); sh.setFormatter(fmt); logger.addHandler(sh)
fh = RotatingFileHandler('bot_audit.log', maxBytes=5*1024*1024, backupCount=3)
fh.setFormatter(fmt); logger.addHandler(fh)

TRADING_FEE = 0.001
SLIPPAGE_DEFAULT = 0.003
TOTAL_COST = TRADING_FEE + SLIPPAGE_DEFAULT

EXCHANGES = {
    'binance': 'https://api.binance.com/api/v3',
    'binance_futures': 'https://fapi.binance.com/fapi/v1',
    'mexc': 'https://api.mexc.com/api/v3'
}
DEX_URL = 'https://api.dexscreener.com/latest/dex'
CG_URL = 'https://api.coingecko.com/api/v3'

HTTP_SEM = asyncio.Semaphore(20)

EXCLUDED = ['UP', 'DOWN', 'BULL', 'BEAR', 'LONG', 'SHORT', 'MOON']
STABLES = ['USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'FDUSD', 'EUR', 'GBP', 'AUD', 'BRL', 'TRY']

CG_MAP = {
    'BTC': 'bitcoin', 'ETH': 'ethereum', 'BNB': 'binancecoin', 'SOL': 'solana',
    'XRP': 'ripple', 'ADA': 'cardano', 'DOGE': 'dogecoin', 'AVAX': 'avalanche-2',
    'DOT': 'polkadot', 'MATIC': 'matic-network', 'LINK': 'chainlink', 'UNI': 'uniswap',
    'LTC': 'litecoin', 'BCH': 'bitcoin-cash', 'ATOM': 'cosmos', 'FIL': 'filecoin',
    'ETC': 'ethereum-classic', 'NEAR': 'near', 'APT': 'aptos', 'ARB': 'arbitrum',
    'OP': 'optimism', 'SUI': 'sui', 'SEI': 'sei-network', 'TIA': 'celestia',
    'INJ': 'injective-protocol', 'FET': 'fetch-ai', 'RNDR': 'render-token',
    'AAVE': 'aave', 'PEPE': 'pepe', 'SHIB': 'shiba-inu', 'TRX': 'tron',
    'ICP': 'internet-computer', 'ALGO': 'algorand', 'XLM': 'stellar', 'VET': 'vechain'
}

# ==========================================
# 🛡️ Rate Limiting
# ==========================================
_user_calls: Dict[int, float] = defaultdict(float)

def user_rate_ok(uid: int, sec: int = 20) -> bool:
    now = time.time()
    if now - _user_calls[uid] < sec: return False
    _user_calls[uid] = now
    return True

async def http_req(client: httpx.AsyncClient, method: str, url: str, **kw):
    async with HTTP_SEM:
        await asyncio.sleep(0.05)
        try:
            r = await client.request(method, url, **kw)
            if r.status_code == 429:
                wait = int(r.headers.get('Retry-After', 5))
                logger.warning(f"⏳ Rate limit: {url} → wait {wait}s")
                await asyncio.sleep(wait)
                return await http_req(client, method, url, **kw)
            return r
        except httpx.TimeoutException:
            logger.warning(f"⏳ Timeout: {url}")
            return None
        except Exception as e:
            logger.error(f"❌ HTTP error {url}: {e}")
            return None

# ==========================================
# 🕐 زمان
# ==========================================
def get_time_info() -> Dict[str, str]:
    now_utc = datetime.now(timezone.utc)
    now_iran = now_utc.astimezone(TEHRAN_TZ)
    now_hijri = jdatetime.datetime.fromgregorian(datetime=now_utc)
    return {
        "iran": now_iran.strftime("%Y-%m-%d %H:%M"),
        "hijri": now_hijri.strftime("%Y/%m/%d %H:%M"),
    }

# ==========================================
# 🗄️ دیتابیس
# ==========================================
def _init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT UNIQUE, entry_price REAL,
        stop_loss REAL, take_profit_1 REAL, take_profit_2 REAL, take_profit_3 REAL,
        z_score REAL, score INTEGER, entry_time TEXT, status TEXT DEFAULT 'open',
        position_size REAL DEFAULT 0, risk_reward REAL DEFAULT 0,
        pump_type TEXT DEFAULT 'unknown', exchange TEXT DEFAULT 'unknown',
        expected_pump TEXT DEFAULT 'N/A', tier TEXT DEFAULT 'N/A',
        funding_rate REAL DEFAULT 0, oi_change REAL DEFAULT 0,
        rsi REAL DEFAULT 0, macd_signal TEXT DEFAULT 'neutral'
    )""")
    c.execute("PRAGMA table_info(positions)")
    cols = [r[1] for r in c.fetchall()]
    for col, sql in {
        'position_size': "ALTER TABLE positions ADD COLUMN position_size REAL DEFAULT 0",
        'risk_reward': "ALTER TABLE positions ADD COLUMN risk_reward REAL DEFAULT 0",
        'pump_type': "ALTER TABLE positions ADD COLUMN pump_type TEXT DEFAULT 'unknown'",
        'exchange': "ALTER TABLE positions ADD COLUMN exchange TEXT DEFAULT 'unknown'",
        'expected_pump': "ALTER TABLE positions ADD COLUMN expected_pump TEXT DEFAULT 'N/A'",
        'tier': "ALTER TABLE positions ADD COLUMN tier TEXT DEFAULT 'N/A'",
        'funding_rate': "ALTER TABLE positions ADD COLUMN funding_rate REAL DEFAULT 0",
        'oi_change': "ALTER TABLE positions ADD COLUMN oi_change REAL DEFAULT 0",
        'rsi': "ALTER TABLE positions ADD COLUMN rsi REAL DEFAULT 0",
        'macd_signal': "ALTER TABLE positions ADD COLUMN macd_signal TEXT DEFAULT 'neutral'"
    }.items():
        if col not in cols:
            try: c.execute(sql); conn.commit()
            except: pass
    conn.close()

def _save_pos(**kw):
    conn = sqlite3.connect(DB_PATH)
    cols = ', '.join(kw.keys())
    ph = ', '.join(['?'] * len(kw))
    conn.execute(f"INSERT OR REPLACE INTO positions ({cols}, status) VALUES ({ph}, 'open')",
                 tuple(kw.values()))
    conn.commit(); conn.close()

async def save_position(**kw):
    await asyncio.to_thread(_save_pos, **kw)

def _get_positions():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    p = [dict(r) for r in conn.execute("SELECT * FROM positions WHERE status='open'")]
    conn.close(); return p

async def get_active_positions():
    return await asyncio.to_thread(_get_positions)

def _upd_status(coin, status):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE positions SET status=? WHERE coin=?", (status, coin))
    conn.commit(); conn.close()

async def update_position_status(c, s):
    await asyncio.to_thread(_upd_status, c, s)

# ==========================================
# 📊 دریافت قیمت CEX
# ==========================================
async def get_cex_price(symbol: str, client: httpx.AsyncClient) -> Dict:
    s = symbol + 'USDT'; r = {}
    for name, base in [('binance', EXCHANGES['binance']), ('mexc', EXCHANGES['mexc'])]:
        try:
            resp = await http_req(client, "GET", f"{base}/ticker/24hr?symbol={s}")
            if not resp or resp.status_code != 200: continue
            d = resp.json()
            r[name] = {'price': float(d['lastPrice']),
                       'change': float(d['priceChangePercent']),
                       'volume': float(d['quoteVolume'])}
        except: pass
    return r

# ==========================================
# 🦄 DEX Coverage بهبودیافته
# ==========================================
async def get_dex_hot_coins() -> List[Dict]:
    """دریافت کوین‌های داغ از چندین منبع DEX"""
    dex_coins = []
    seen_symbols = set()
    
    endpoints = [
        ('search?q=pump', 'pump'),
        ('search?q=trending', 'trending'),
        ('search?q=new', 'new'),
        ('search?q=gainer', 'gainer'),
    ]
    
    for endpoint, source in endpoints:
        try:
            async with httpx.AsyncClient(timeout=30) as cl:
                r = await http_req(cl, "GET", f"{DEX_URL}/{endpoint}")
                if not r or r.status_code != 200: continue
                
                pairs = r.json().get('pairs', [])
                for p in pairs[:100]:
                    sym = p.get('baseToken', {}).get('symbol', '')
                    if not sym or sym in seen_symbols: continue
                    
                    vol = float(p.get('volume', {}).get('h24', 0) or 0)
                    chg = float(p.get('priceChange', {}).get('h24', 0) or 0)
                    liq = float(p.get('liquidity', {}).get('usd', 0) or 0)
                    mc = float(p.get('marketCap', 0) or 0)
                    
                    if vol < 1000: continue
                    if liq < 1000: continue
                    if any(sym.endswith(e) for e in EXCLUDED): continue
                    if sym in STABLES: continue
                    
                    seen_symbols.add(sym)
                    dex_coins.append({
                        'symbol': sym,
                        'volume': vol,
                        'change': chg,
                        'liquidity': liq,
                        'market_cap': mc,
                        'src': 'DEX',
                        'dex': p.get('dexId', '?'),
                        'chain': p.get('chainId', '?')
                    })
        except Exception as e:
            logger.warning(f"DEX endpoint {source} error: {e}")
            continue
    
    dex_coins.sort(key=lambda x: x['volume'], reverse=True)
    return dex_coins[:500]

async def get_dex_data(symbol: str, client: httpx.AsyncClient) -> Optional[Dict]:
    try:
        resp = await http_req(client, "GET", f"{DEX_URL}/search?q={symbol}")
        if not resp or resp.status_code != 200: return None
        pairs = resp.json().get('pairs', [])
        su = symbol.upper()
        fp = [p for p in pairs
              if p.get('baseToken', {}).get('symbol', '').upper() == su
              and p.get('quoteToken', {}).get('symbol', '').upper() in ['USDT','USDC','WETH','WBNB','SOL']]
        if not fp: return None
        fp.sort(key=lambda x: float(x.get('volume',{}).get('h24',0) or 0), reverse=True)
        b = fp[0]
        liq = float(b.get('liquidity',{}).get('usd',0) or 0)
        if liq < 1000: return None
        return {
            'dex': b.get('dexId','?'),
            'price': float(b.get('priceUsd',0) or 0),
            'volume_24h': float(b.get('volume',{}).get('h24',0) or 0),
            'liquidity': liq,
            'change_24h': float(b.get('priceChange',{}).get('h24',0) or 0)
        }
    except Exception as e:
        logger.error(f"DEX error {symbol}: {e}")
        return None

# ==========================================
# 📈 دریافت کندل
# ==========================================
async def get_klines(symbol: str, client: httpx.AsyncClient,
                     interval: str = '1h', limit: int = 168) -> Optional[List]:
    try:
        r = await http_req(client, "GET",
            f"{EXCHANGES['binance']}/klines?symbol={symbol}USDT&interval={interval}&limit={limit}")
        if r and r.status_code == 200:
            data = r.json()
            if len(data) >= 20:
                return [{'close': float(k[4]), 'high': float(k[2]),
                         'low': float(k[3]), 'vol': float(k[7])} for k in data]
    except: pass
    try:
        r = await http_req(client, "GET",
            f"{EXCHANGES['mexc']}/klines?symbol={symbol}USDT&interval={interval}&limit={limit}")
        if r and r.status_code == 200:
            data = r.json().get('data', [])
            if len(data) >= 20:
                return [{'close': float(k.get('close',0)), 'high': float(k.get('high',0)),
                         'low': float(k.get('low',0)), 'vol': float(k.get('vol',0))} for k in data]
    except: pass
    return None

# ==========================================
# 📊 Z-Score
# ==========================================
async def calc_z_scores(symbol: str, client: httpx.AsyncClient) -> Dict[str, Tuple[float, float]]:
    results = {}
    for iv, lim in [('1h', 168), ('4h', 42), ('1d', 14)]:
        klines = await get_klines(symbol, client, iv, lim)
        if not klines or len(klines) < 20:
            results[iv] = (0.0, 0.0); continue
        vols = np.array([k['vol'] for k in klines[:-1]])
        cur = klines[-1]['vol']
        m, s = vols.mean(), vols.std(ddof=1)
        if s == 0:
            results[iv] = (0.0, 0.0); continue
        results[iv] = (float((cur - m) / s), float(cur / m if m > 0 else 0))
    return results

# ==========================================
# 📉 اندیکاتورها
# ==========================================
def calc_rsi_array(prices: List[float], period: int = 14) -> List[float]:
    if len(prices) < period + 1:
        return [50.0] * len(prices)
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    rsi_vals = [50.0] * period
    ag = np.mean(gains[:period])
    al = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        rs = ag / al if al != 0 else 100
        rsi_vals.append(100 - (100 / (1 + rs)))
    return rsi_vals

def calc_rsi_val(prices: List[float], period: int = 14) -> float:
    arr = calc_rsi_array(prices, period)
    return arr[-1] if arr else 50.0

def calc_macd(prices: List[float]) -> Dict:
    if len(prices) < 35:
        return {'macd': 0, 'signal': 0, 'hist': 0, 'cross': 'none'}
    s = pd.Series(prices)
    e12 = s.ewm(span=12, adjust=False).mean()
    e26 = s.ewm(span=26, adjust=False).mean()
    ml = e12 - e26
    sl = ml.ewm(span=9, adjust=False).mean()
    cross = 'none'
    if ml.iloc[-1] > sl.iloc[-1] and ml.iloc[-2] <= sl.iloc[-2]: cross = 'bullish_cross'
    elif ml.iloc[-1] < sl.iloc[-1] and ml.iloc[-2] >= sl.iloc[-2]: cross = 'bearish_cross'
    elif ml.iloc[-1] > sl.iloc[-1]: cross = 'bullish'
    else: cross = 'bearish'
    return {'macd': ml.iloc[-1], 'signal': sl.iloc[-1],
            'hist': ml.iloc[-1] - sl.iloc[-1], 'cross': cross}

def calc_bollinger(prices: List[float], period=20, std_dev=2) -> Dict:
    if len(prices) < period:
        return {'upper': 0, 'middle': 0, 'lower': 0}
    s = pd.Series(prices).tail(period)
    m = s.mean(); sd = s.std()
    return {'upper': m + sd * std_dev, 'middle': m, 'lower': m - sd * std_dev}

# ==========================================
# 📐 ATR
# ==========================================
async def get_atr(symbol: str, client: httpx.AsyncClient, period: int = 14) -> Optional[float]:
    klines = await get_klines(symbol, client, '1h', period + 2)
    if not klines or len(klines) < period + 1: return None
    data = klines[:-1]
    trs = [max(d['high']-d['low'],
               abs(d['high']-data[i-1]['close']),
               abs(d['low']-data[i-1]['close']))
           for i, d in enumerate(data[1:], 1)]
    return float(np.mean(trs)) if trs else None

# ==========================================
# 🔍 تشخیص الگوها
# ==========================================
def detect_accumulation(closes: List[float], atr: Optional[float], avg_price: float) -> bool:
    if len(closes) < 10 or not atr or avg_price == 0: return False
    recent = closes[-10:]
    rng = (max(recent) - min(recent)) / avg_price
    return rng < 0.02 and (atr / avg_price) < 0.015

def detect_divergence(closes: List[float], rsi_arr: List[float]) -> str:
    if len(closes) < 20 or len(rsi_arr) < 20: return 'none'
    rp, rr = closes[-20:], rsi_arr[-20:]
    peaks_p, peaks_r = [], []
    for i in range(2, len(rp) - 2):
        if rp[i] > rp[i-1] and rp[i] > rp[i-2] and rp[i] > rp[i+1] and rp[i] > rp[i+2]:
            peaks_p.append(rp[i]); peaks_r.append(rr[i])
    if len(peaks_p) >= 2:
        if peaks_p[-1] > peaks_p[-2] and peaks_r[-1] < peaks_r[-2]:
            return 'bearish'
    return 'none'

def detect_price_acceleration(closes: List[float]) -> bool:
    if len(closes) < 4: return False
    r1 = (closes[-1] / closes[-2] - 1) * 100 if closes[-2] > 0 else 0
    r3 = (closes[-1] / closes[-4] - 1) * 100 if closes[-4] > 0 else 0
    return r1 > 10 or r3 > 15

def detect_whale_dist(vol_24h: float, change: float, tier: str) -> bool:
    min_v = {'Mega': 100_000_000, 'Large': 20_000_000, 'Mid': 5_000_000, 'Small': 500_000}.get(tier, 1_000_000)
    return vol_24h > min_v * 3 and -5 <= change <= 2

def detect_stop_hunt(closes: List[float], cur_price: float) -> bool:
    if len(closes) < 5: return False
    low5 = min(closes[-5:])
    return cur_price > low5 and closes[-2] <= low5

def check_btc_corr(alt_chg: float, btc_chg: float) -> str:
    if btc_chg < -2 and alt_chg > 5: return 'suspicious'
    return 'normal'

async def get_orderbook_imbalance(symbol: str, client: httpx.AsyncClient) -> Tuple[float, str]:
    try:
        r = await http_req(client, "GET",
            f"{EXCHANGES['binance']}/depth?symbol={symbol}USDT&limit=20")
        if not r or r.status_code != 200: return 0.0, "⚪ نامشخص"
        d = r.json()
        bids = sum(float(b[1]) for b in d.get('bids', []))
        asks = sum(float(a[1]) for a in d.get('asks', []))
        if bids + asks == 0: return 0.0, "⚪ خالی"
        imb = (bids - asks) / (bids + asks)
        if imb > 0.3: st = "🟢 فشار خرید"
        elif imb < -0.3: st = "🔴 فشار فروش"
        else: st = "⚪ متعادل"
        return imb, st
    except:
        return 0.0, "⚪ خطا"

# ==========================================
# 📊 داده‌های مشتقات
# ==========================================
async def get_funding(symbol: str, client: httpx.AsyncClient) -> Optional[Dict]:
    try:
        r = await http_req(client, "GET",
            f"{EXCHANGES['binance_futures']}/fundingRate?symbol={symbol}USDT&limit=10")
        if not r or r.status_code != 200: return None
        d = r.json()
        if not d: return None
        cur = float(d[-1]['fundingRate'])
        avg = float(np.mean([float(x['fundingRate']) for x in d]))
        sig = 'bullish' if cur < 0 else ('bearish' if cur > 0.01 else 'neutral')
        return {'current': cur, 'average': avg, 'signal': sig}
    except: return None

async def get_oi_change(symbol: str, client: httpx.AsyncClient) -> Optional[Dict]:
    try:
        r1 = await http_req(client, "GET",
            f"{EXCHANGES['binance_futures']}/openInterest?symbol={symbol}USDT")
        if not r1 or r1.status_code != 200: return None
        cur_oi = float(r1.json()['openInterest'])
        r2 = await http_req(client, "GET",
            f"{EXCHANGES['binance_futures']}/openInterestHist?symbol={symbol}USDT&period=1h&limit=24")
        chg = 0
        if r2 and r2.status_code == 200:
            h = r2.json()
            if len(h) >= 2:
                old = float(h[0]['sumOpenInterest'])
                chg = ((cur_oi - old) / old * 100) if old > 0 else 0
        sig = 'bullish' if chg > 10 else ('bearish' if chg < -10 else 'neutral')
        return {'change_24h': chg, 'signal': sig}
    except: return None

# ==========================================
# 😱 حس بازار + CoinGecko
# ==========================================
async def get_market_sentiment(client: httpx.AsyncClient) -> Optional[Dict]:
    try:
        r = await http_req(client, "GET", "https://api.alternative.me/fng/?limit=1")
        if not r or r.status_code != 200: return None
        d = r.json()
        if not d or not d.get('data'): return None
        v = int(d['data'][0]['value'])
        if v <= 25: e, s = "😱", "ترس شدید"
        elif v <= 45: e, s = "😨", "ترس"
        elif v <= 55: e, s = "😐", "خنثی"
        elif v <= 75: e, s = "😊", "طمع"
        else: e, s = "🤑", "طمع شدید"
        return {'value': v, 'emoji': e, 'status': s}
    except: return None

async def get_cg_id(symbol: str, client: httpx.AsyncClient) -> Optional[str]:
    if symbol in CG_MAP: return CG_MAP[symbol]
    try:
        r = await http_req(client, "GET", f"{CG_URL}/search?query={symbol}")
        if r and r.status_code == 200:
            for c in r.json().get('coins', []):
                if c.get('symbol','').upper() == symbol:
                    return c.get('id')
    except: pass
    return None

async def get_market_cap(symbol: str, client: httpx.AsyncClient) -> Optional[float]:
    try:
        cid = await get_cg_id(symbol, client)
        if not cid: return None
        r = await http_req(client, "GET",
            f"{CG_URL}/coins/{cid}?localization=false&tickers=false&market_data=true")
        if r and r.status_code == 200:
            return r.json().get('market_data',{}).get('market_cap',{}).get('usd')
    except: pass
    return None

async def get_supply(symbol: str, client: httpx.AsyncClient) -> Optional[Dict]:
    try:
        cid = await get_cg_id(symbol, client)
        if not cid: return None
        r = await http_req(client, "GET",
            f"{CG_URL}/coins/{cid}?localization=false&tickers=false&market_data=true")
        if not r or r.status_code != 200: return None
        md = r.json().get('market_data', {})
        circ = md.get('circulating_supply', 0) or 0
        total = md.get('total_supply', 0) or 0
        ratio = (circ / total) if (circ > 0 and total > 0) else 1.0
        return {'circulating': circ, 'total': total,
                'max': md.get('max_supply', 0) or 0,
                'ratio': ratio, 'low_float': ratio < 0.5}
    except: return None

# ==========================================
# 🔍 جستجوی اجتماعی
# ==========================================
def search_social(coin: str) -> List[Dict]:
    res = []
    try:
        from ddgs import DDGS
        with DDGS() as d:
            for q in [f"{coin} crypto site:x.com", f"{coin} crypto site:t.me"]:
                try:
                    for r in list(d.text(q, max_results=5)):
                        t, b, h = r.get('title',''), r.get('body',''), r.get('href','')
                        if coin.lower() in (t+b).lower():
                            u = "?"
                            if 'x.com/' in h or 'twitter.com/' in h:
                                m = re.search(r'(?:x\.com|twitter\.com)/(\w+)', h)
                                if m: u = m.group(1)
                            elif 't.me/' in h:
                                m = re.search(r't\.me/(\w+)', h)
                                if m: u = m.group(1)
                            if u not in [x['username'] for x in res]:
                                p = "توییتر" if 'x.com' in h or 'twitter.com' in h else "تلگرام"
                                res.append({"username": u, "platform": p,
                                           "comment": (b or t)[:100], "link": h})
                except: pass
    except: pass
    return res

# ==========================================
# 🎯 Tier + امتیازدهی
# ==========================================
def get_tier(mc: float) -> Dict:
    if mc > 10e9:
        return {'name': 'Mega', 'z': 1.0, 'mult': 1.2, 'iv': '4h', 'lim': 42,
                'min_vol': 50e6, 'slip': 0.001}
    elif mc > 1e9:
        return {'name': 'Large', 'z': 1.2, 'mult': 1.5, 'iv': '4h', 'lim': 42,
                'min_vol': 10e6, 'slip': 0.002}
    elif mc > 100e6:
        return {'name': 'Mid', 'z': 1.5, 'mult': 2.0, 'iv': '1h', 'lim': 168,
                'min_vol': 2e6, 'slip': 0.005}
    else:
        return {'name': 'Small', 'z': 2.0, 'mult': 3.0, 'iv': '1h', 'lim': 168,
                'min_vol': 200e3, 'slip': 0.01}

def estimate_mc(vol: float) -> float:
    return vol * 20

def score_coin(z_scores, change, vol, fr, oi, rsi, macd_cross, liq, tier_name,
               accum, divg, stop_h, price_acc, whale_d, ob_imb, btc_corr):
    s = 0
    z4 = z_scores.get('4h', (0,0))[0]
    z1 = z_scores.get('1h', (0,0))[0]
    m4 = z_scores.get('4h', (0,0))[1]

    if z4 >= 3: s += 25
    elif z4 >= 2: s += 20
    elif z4 >= 1.5: s += 12
    elif z1 >= 2: s += 8

    if m4 >= 5: s += 15
    elif m4 >= 3: s += 12
    elif m4 >= 2: s += 8

    if -15 <= change <= 25: s += 10
    elif -20 <= change <= 35: s += 5

    if vol > 100_000: s += 5

    if fr is not None:
        if fr < -0.01: s += 10
        elif fr < 0.01: s += 5

    if oi is not None:
        if oi > 20: s += 10
        elif oi > 10: s += 5

    if rsi and 40 <= rsi <= 60: s += 5
    elif rsi and 30 <= rsi < 40: s += 3

    if macd_cross == 'bullish_cross': s += 5
    elif macd_cross == 'bullish': s += 3

    if liq > 100_000: s += 5
    elif liq > 50_000: s += 3

    if accum: s += 10
    if divg == 'bearish': s -= 10
    if stop_h: s += 5
    if price_acc: s += 5
    if whale_d: s -= 15
    if abs(ob_imb) > 0.5: s += 3
    if btc_corr == 'suspicious': s -= 10

    if s >= 75 and z4 >= 2 and -15 <= change <= 25 and accum:
        pt = "BTR Classic"
    elif s >= 65 and z4 >= 1.5:
        pt = "BTR-like"
    elif s >= 55 and (z1 >= 2 or m4 >= 2.5):
        pt = "Volume Spike"
    elif s >= 55 and accum:
        pt = "Accumulation"
    elif s >= 45 and liq > 1000:
        pt = "DEX Gem"
    else:
        pt = "Monitor"

    return s, pt

def predict_pump(s, liq):
    base = s * 2
    if liq > 0: base *= min(1.5, liq / 50000)
    return {
        'min': round(max(5, base * 0.5), 1),
        'likely': round(base, 1),
        'max': round(min(500, base * 2.5), 1),
        'conf': "بالا" if s >= 70 else ("متوسط" if s >= 50 else "پایین")
    }

# ==========================================
# 🎯 تحلیل کامل
# ==========================================
async def analyze_full(raw: str) -> Tuple[str, Optional[Dict]]:
    sym = raw.upper().strip().replace('USDT', '')
    ti = get_time_info()

    async with httpx.AsyncClient(timeout=30) as cl:
        cex, dex, atr, sent, fr, oi, supply, mc, ob_imb, zs, btc_ticker = await asyncio.gather(
            get_cex_price(sym, cl),
            get_dex_data(sym, cl),
            get_atr(sym, cl),
            get_market_sentiment(cl),
            get_funding(sym, cl),
            get_oi_change(sym, cl),
            get_supply(sym, cl),
            get_market_cap(sym, cl),
            get_orderbook_imbalance(sym, cl),
            calc_z_scores(sym, cl),
            http_req(cl, "GET", f"{EXCHANGES['binance']}/ticker/24hr?symbol=BTCUSDT")
        )

    ob_imb_val, ob_status = ob_imb

    if not cex and dex:
        cex = {'dex': {'price': dex['price'], 'change': dex['change_24h'],
                       'volume': dex['volume_24h']}}
    if not cex:
        return f"❌ {escape(sym)} یافت نشد", None

    tv = sum(x['volume'] for x in cex.values())
    ap = sum(x['price']*x['volume'] for x in cex.values()) / tv if tv > 0 else np.mean([x['price'] for x in cex.values()])
    ac = np.mean([x['change'] for x in cex.values()])

    est_mc = mc if mc else estimate_mc(tv)
    tier = get_tier(est_mc)
    liq = dex['liquidity'] if dex else 0

    klines = await get_klines(sym, cl, '1h', 50)
    closes = []
    rsi_val, macd_data, boll, rsi_arr = 50.0, None, None, []
    accum, divg, stop_h, price_acc, whale_d = False, 'none', False, False, False

    if klines and len(klines) >= 20:
        closes = [k['close'] for k in klines[:-1]]
        rsi_arr = calc_rsi_array(closes)
        rsi_val = rsi_arr[-1] if rsi_arr else 50.0
        macd_data = calc_macd(closes)
        boll = calc_bollinger(closes)
        accum = detect_accumulation(closes, atr, ap)
        divg = detect_divergence(closes, rsi_arr)
        stop_h = detect_stop_hunt(closes, ap)
        price_acc = detect_price_acceleration(closes)
        whale_d = detect_whale_dist(tv, ac, tier['name'])

    btc_chg = 0
    try:
        if btc_ticker and btc_ticker.status_code == 200:
            btc_chg = float(btc_ticker.json()['priceChangePercent'])
    except: pass
    btc_corr = check_btc_corr(ac, btc_chg)

    fr_val = fr['current'] if fr else None
    oi_val = oi['change_24h'] if oi else None
    macd_cross = macd_data['cross'] if macd_data else 'none'

    score, ptype = score_coin(
        zs, ac, tv, fr_val, oi_val, rsi_val, macd_cross, liq, tier['name'],
        accum, divg, stop_h, price_acc, whale_d, ob_imb_val, btc_corr
    )

    pp = predict_pump(score, liq)

    slip = tier['slip']
    cost_f = 1 - (TRADING_FEE + slip)
    if atr and atr > 0:
        sl = (ap - 2*atr) * cost_f
        t1 = (ap + 2*atr) * (1 - TRADING_FEE - slip)
        t2 = (ap + 4*atr) * (1 - TRADING_FEE - slip)
        t3 = (ap + 8*atr) * (1 - TRADING_FEE - slip)
    else:
        sl = ap * 0.80 * cost_f
        t1 = ap * 1.30 * (1 - TRADING_FEE - slip)
        t2 = ap * 1.60 * (1 - TRADING_FEE - slip)
        t3 = ap * 2.20 * (1 - TRADING_FEE - slip)

    rm = RiskManager(ACCOUNT_BALANCE, RISK_PER_TRADE, MAX_POSITIONS)
    z4 = zs.get('4h', (0,0))[0]
    ps = rm.calculate_position_size(ap, sl, z4, liq)
    rr = rm.calculate_risk_reward(ap, sl, t3)

    if score >= 70 and accum: st = "🟢 سیگنال قوی (انباشت + حجم)"
    elif score >= 60: st = "🟢 سیگنال قوی"
    elif score >= 50: st = "🟡 سیگنال متوسط"
    elif whale_d: st = "⚫ هشدار توزیع نهنگ!"
    else: st = "⚪ عادی"

    ex_names = list(cex.keys())
    if dex: ex_names.append(dex['dex'])
    ex_str = " + ".join(ex_names)

    r = f"🔍 <b>{escape(sym)}</b>\n⏰ {ti['iran']}\n🏪 {ex_str}\n\n"
    r += f"💰 ${ap:,.6f} ({ac:+.2f}%)\n"
    r += f"📈 Z: 1h:{zs.get('1h',(0,0))[0]:.2f} | 4h:{z4:.2f} | 1d:{zs.get('1d',(0,0))[0]:.2f}\n"
    r += f"📊 Vol Mult: {zs.get('4h',(0,0))[1]:.1f}x\n"
    r += f"📉 RSI: {rsi_val:.1f}\n"
    if macd_data:
        r += f"📊 MACD: {macd_data['cross']} (hist: {macd_data['hist']:.4f})\n"
    if boll and boll['upper'] > 0:
        pct_b = (ap - boll['lower']) / (boll['upper'] - boll['lower']) if (boll['upper']-boll['lower']) > 0 else 0
        r += f"📊 Bollinger %B: {pct_b*100:.1f}%\n"
    r += f"🎯 {st}\n\n"

    r += f"🔎 <b>الگوها:</b>\n"
    r += f"• انباشت: {'✅' if accum else '❌'}\n"
    r += f"• واگرایی: {'⚠️ نزولی' if divg=='bearish' else '✅ ندارد'}\n"
    r += f"• شتاب قیمت: {'✅' if price_acc else '❌'}\n"
    r += f"• شکار استاپ: {'⚠️' if stop_h else '✅'}\n"
    r += f"• توزیع نهنگ: {'⚠️ بله!' if whale_d else '✅ خیر'}\n"
    r += f"• Order Book: {ob_status} ({ob_imb_val:+.2f})\n"
    r += f"• همبستگی BTC: {'⚠️ مشکوک' if btc_corr=='suspicious' else '✅ عادی'} (BTC: {btc_chg:+.1f}%)\n\n"

    r += f"📊 <b>مشتقات:</b>\n"
    if fr:
        fe = "🟢" if fr['signal']=='bullish' else ("🔴" if fr['signal']=='bearish' else "⚪")
        r += f"• {fe} Funding: {fr['current']*100:.4f}%\n"
    if oi:
        oe = "🟢" if oi['signal']=='bullish' else ("🔴" if oi['signal']=='bearish' else "⚪")
        r += f"• {oe} OI 24h: {oi['change_24h']:+.2f}%\n"
    r += "\n"

    if supply:
        r += f"💰 <b>عرضه:</b> {supply['ratio']*100:.1f}% در گردش"
        r += f" | Low Float: {'✅' if supply['low_float'] else '❌'}\n\n"

    r += f"🚀 <b>پیش‌بینی پامپ:</b>\n"
    r += f"• {pp['min']}% - <b>{pp['likely']}%</b> - {pp['max']}%\n"
    r += f"• اطمینان: {pp['conf']}\n\n"

    if sent:
        r += f"{sent['emoji']} {sent['status']} ({sent['value']}/100)\n\n"

    r += f"💵 <b>مدیریت ریسک ({tier['name']}, هزینه {(TRADING_FEE+slip)*100:.2f}%):</b>\n"
    r += f"• ورود: ${ap:,.6f}\n"
    r += f"• استاپ: ${sl:,.6f}\n"
    r += f"• T1: ${t1:,.6f} | T2: ${t2:,.6f} | T3: ${t3:,.6f}\n"
    r += f"• حجم: ${ps:,.2f} | R:R: 1:{rr:.2f}\n\n"

    r += f"🎯 <b>امتیاز: {score}/100</b>\n"
    if score >= 70: r += "✅ سیگنال قوی → ورود با ۲٪ ریسک\n"
    elif score >= 55: r += "⚠️ متوسط → ورود با ۱٪ ریسک\n"
    else: r += "❌ ضعیف → ورود توصیه نمی‌شود\n"

    social = await asyncio.to_thread(search_social, sym)
    if social:
        r += f"\n💬 <b>شبکه‌های اجتماعی:</b>\n"
        for i, s in enumerate(social[:3], 1):
            e = "🐦" if s['platform']=='توییتر' else "✈️"
            r += f"{i}. {e} {s['username']}: <i>{s['comment'][:60]}...</i>\n"

    r += f"\n⚠️ <i>تحلیل کمی ≠ توصیه مالی</i>"

    pd_ = {
        'coin': sym, 'entry_price': ap, 'stop_loss': sl,
        'take_profit_1': t1, 'take_profit_2': t2, 'take_profit_3': t3,
        'z_score': z4, 'score': score, 'tier': tier['name'],
        'entry_time': ti['iran'], 'expected_pump': f"{pp['likely']}%",
        'funding_rate': fr_val or 0, 'oi_change': oi_val or 0,
        'position_size': ps, 'risk_reward': rr,
        'pump_type': ptype, 'exchange': ex_str,
        'rsi': rsi_val, 'macd_signal': macd_cross
    }
    return r, pd_

# ==========================================
# ⚡ اسکن سریع - با DEX Coverage بهبودیافته
# ==========================================
async def quick_scan() -> Optional[str]:
    try:
        # ✅ DEX Coverage بهبودیافته
        dex_coins = await get_dex_hot_coins()
        logger.info(f"🦄 {len(dex_coins)} کوین DEX یافت شد")

        # Binance
        bn_coins = []
        try:
            async with httpx.AsyncClient(timeout=60) as cl:
                br = await http_req(cl, "GET", f"{EXCHANGES['binance']}/ticker/24hr")
                if br and br.status_code == 200:
                    df = pd.DataFrame(br.json())
                    df['priceChangePercent'] = pd.to_numeric(df['priceChangePercent'])
                    df['quoteVolume'] = pd.to_numeric(df['quoteVolume'])
                    df = df[df['symbol'].str.endswith('USDT')]
                    df = df[df['quoteVolume'] > 50000]
                    df = df[(df['priceChangePercent'] >= -60) & (df['priceChangePercent'] <= 500)]
                    for _, row in df.iterrows():
                        s = row['symbol'].replace('USDT','')
                        if not any(s.endswith(e) for e in EXCLUDED) and s not in STABLES:
                            bn_coins.append({'symbol':s,'volume':row['quoteVolume'],'change':row['priceChangePercent'],'src':'BN'})
        except: pass

        # MEXC
        mx_coins = []
        try:
            async with httpx.AsyncClient(timeout=60) as cl:
                mr = await http_req(cl, "GET", f"{EXCHANGES['mexc']}/ticker/24hr")
                if mr and mr.status_code == 200:
                    df = pd.DataFrame(mr.json())
                    df['priceChangePercent'] = pd.to_numeric(df['priceChangePercent'])
                    df['quoteVolume'] = pd.to_numeric(df['quoteVolume'])
                    df = df[df['symbol'].str.endswith('USDT')]
                    df = df[df['quoteVolume'] > 10000]
                    df = df[(df['priceChangePercent'] >= -60) & (df['priceChangePercent'] <= 500)]
                    for _, row in df.iterrows():
                        s = row['symbol'].replace('USDT','')
                        if not any(s.endswith(e) for e in EXCLUDED) and s not in STABLES:
                            mx_coins.append({'symbol':s,'volume':row['quoteVolume'],'change':row['priceChangePercent'],'src':'MX'})
        except: pass

        # ترکیب
        all_c = {}
        for c in dex_coins + bn_coins + mx_coins:
            if c['symbol'] not in all_c or c['volume'] > all_c[c['symbol']]['volume']:
                all_c[c['symbol']] = c
        all_c = dict(sorted(all_c.items(), key=lambda x: x[1]['volume'], reverse=True)[:1000])

        logger.info(f"📊 {len(all_c)} کوین (BN:{len(bn_coins)} MX:{len(mx_coins)} DEX:{len(dex_coins)})")

        final = []
        checked = 0
        
        all_items = list(all_c.items())
        batch_size = 100
        
        for batch_start in range(0, len(all_items), batch_size):
            batch = all_items[batch_start:batch_start + batch_size]
            
            async with httpx.AsyncClient(timeout=300) as batch_cl:
                for sym, cd in batch:
                    checked += 1
                    
                    try:
                        zs = {'1h':(0,0),'4h':(0,0),'1d':(0,0)}
                        if cd['src'] != 'DEX':
                            zs = await calc_z_scores(sym, batch_cl)

                        z4, m4 = zs.get('4h',(0,0))
                        z1 = zs.get('1h',(0,0))[0]
                        if z4 < 0.8 and z1 < 1.0 and m4 < 1.2: continue

                        fr_v = oi_v = rsi_v = None
                        macd_c = 'none'
                        dex_d = None
                        kl = None
                        
                        if cd['src'] != 'DEX':
                            fr_d = await get_funding(sym, batch_cl)
                            fr_v = fr_d['current'] if fr_d else None
                            oi_d = await get_oi_change(sym, batch_cl)
                            oi_v = oi_d['change_24h'] if oi_d else None
                            
                            kl = await get_klines(sym, batch_cl, '1h', 30)
                            if kl and len(kl) >= 20:
                                cls = [k['close'] for k in kl[:-1]]
                                rsi_v = calc_rsi_val(cls)
                                md = calc_macd(cls)
                                macd_c = md['cross'] if md else 'none'
                        else:
                            dex_d = await get_dex_data(sym, batch_cl)

                        liq = dex_d['liquidity'] if dex_d else cd.get('liquidity', 0)
                        tier = get_tier(estimate_mc(cd['volume']))

                        accum = False
                        divg = 'none'
                        stop_h = False
                        price_acc = False
                        whale_d = False
                        
                        if cd['src'] != 'DEX' and kl and len(kl) >= 20:
                            cls = [k['close'] for k in kl[:-1]]
                            atr_est = np.mean([kl[i]['high']-kl[i]['low'] for i in range(max(0,len(kl)-15), len(kl)-1)]) if len(kl) > 1 else 0
                            accum = detect_accumulation(cls, atr_est, cls[-1] if cls else 0)
                            price_acc = detect_price_acceleration(cls)

                        sc, pt = score_coin(
                            zs, cd['change'], cd['volume'], fr_v, oi_v, rsi_v, macd_c, liq, tier['name'],
                            accum, divg, stop_h, price_acc, whale_d, 0, 'normal'
                        )

                        if sc >= 30:
                            pp = predict_pump(sc, liq)
                            final.append({
                                'symbol': sym, 'change': cd['change'], 'z4': round(z4,2),
                                'm4': round(m4,2), 'score': sc, 'pump': f"{pp['likely']}%",
                                'pt': pt, 'src': cd['src'], 'fr': fr_v or 0
                            })
                    except Exception as e:
                        logger.warning(f"⚠️ خطا در {sym}: {e}")
                        continue

            if checked % 100 == 0:
                logger.info(f"📊 {checked}/{len(all_items)}")

        if not final: return None
        final.sort(key=lambda x: (x['score'], x['z4']), reverse=True)

        ti = get_time_info()
        r = f"⚡ <b>اسکن سریع</b>\n⏰ {ti['iran']}\n📊 {checked} کوین\n\n🏆 <b>کاندیداها:</b>\n"
        for c in final[:20]:
            e = "🔥" if c['score'] >= 60 else "⚡"
            s = "🦄" if c['src']=='DEX' else ("🏦" if c['src']=='BN' else "🏪")
            r += f"{e}{s} <b>{c['symbol']}</b> | {c['score']} | Z:{c['z4']:.2f} | {c['pt']} | {c['pump']} | {c['change']:+.1f}%\n"
        r += "\n<i>🔥≥60 | 🦄DEX 🏦BN 🏪MX</i>"
        return r
    except Exception as e:
        logger.error(f"Scan error: {e}")
        return None

# ==========================================
# 🔄 بررسی خروج
# ==========================================
async def check_exit(pos: Dict) -> Tuple[Optional[str], str]:
    coin = pos['coin']
    async with httpx.AsyncClient(timeout=10) as cl:
        cex = await get_cex_price(coin, cl)
        cp = None
        if cex:
            tv = sum(x['volume'] for x in cex.values())
            cp = sum(x['price']*x['volume'] for x in cex.values()) / tv if tv else np.mean([x['price'] for x in cex.values()])
        else:
            dex = await get_dex_data(coin, cl)
            if dex: cp = dex['price']
        if cp is None: return None, "HOLD"

    reasons = []
    if cp <= pos['stop_loss']: reasons.append(f"🔴 استاپ (${pos['stop_loss']:,.4f})")
    if cp >= pos['take_profit_3']: reasons.append(f"🟢 تارگت 3 (${pos['take_profit_3']:,.4f})")

    if reasons:
        pnl = ((cp - pos['entry_price']) / pos['entry_price'] * 100)
        msg = f"🚨 <b>خروج {escape(coin)}</b>\n• ورود: ${pos['entry_price']:,.6f}\n• فعلی: ${cp:,.6f}\n• سود: {pnl:+.2f}%\n\n" + "\n".join(reasons)
        return msg, "EXIT"
    return None, "HOLD"

# ==========================================
# 🤖 Handlers
# ==========================================
async def cmd_start(u, c):
    await u.message.reply_text(
        "🔥 <b>شکارچی پامپ v10.2</b>\n\n"
        "/scan - اسکن CEX+DEX\n"
        "/positions - پوزیشن‌ها\n"
        "/backtest - بک‌تست\n\n"
        "نام کوین → تحلیل کامل\n\n"
        "✅ DEX Coverage بهبودیافته\n"
        "✅ Binance + MEXC + DEX\n"
        "✅ توییتر + تلگرام",
        parse_mode='HTML')

async def cmd_scan(u, c):
    if u.effective_user.id not in ALLOWED_USERS: return
    await u.message.reply_text("⚡ اسکن... (3-4 دقیقه)")
    r = await quick_scan()
    await u.message.reply_text(r or "✅ سیگنالی نیست", parse_mode='HTML')

async def cmd_pos(u, c):
    if u.effective_user.id not in ALLOWED_USERS: return
    ps = await get_active_positions()
    if not ps:
        await u.message.reply_text("📭 خالی"); return
    r = "📊 <b>پوزیشن‌ها:</b>\n\n"
    for p in ps:
        r += f"<b>{escape(p['coin'])}</b> [{p.get('pump_type','?')}]\n"
        r += f"• ${p['entry_price']:,.6f} → استاپ ${p['stop_loss']:,.6f}\n"
        r += f"• پامپ: {p.get('expected_pump','?')} | R:R: 1:{p.get('risk_reward',0):.2f}\n\n"
    await u.message.reply_text(r, parse_mode='HTML')

async def cmd_bt(u, c):
    if u.effective_user.id not in ALLOWED_USERS: return
    await u.message.reply_text("🧪 بک‌تست... (2-3 دقیقه)")
    try:
        from backtest import run_backtest
        r = await asyncio.to_thread(run_backtest)
        await u.message.reply_text(r, parse_mode='HTML')
    except Exception as e:
        await u.message.reply_text(f"❌ {e}")

async def handle_msg(u, c):
    uid = u.effective_user.id
    if uid not in ALLOWED_USERS:
        await u.message.reply_text("⛔"); return
    if not user_rate_ok(uid, 20):
        await u.message.reply_text("⏳ 20s صبر کنید"); return
    t = u.message.text.strip().upper()
    if t.startswith('/'): return
    if len(t) < 2 or len(t) > 10 or not t.isalpha(): return
    await u.message.reply_text(f"⏳ <b>{escape(t)}</b>...", parse_mode='HTML')
    try:
        r, pd_ = await analyze_full(t)
        if pd_ and pd_['score'] >= 60:
            await save_position(**pd_)
            r += "\n\n✅ ذخیره شد."
        for i in range(0, len(r), 4000):
            await u.message.reply_text(r[i:i+4000], parse_mode='HTML', disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Error {t}: {e}")
        await u.message.reply_text(f"❌ {e}")

async def auto_scan(ctx):
    logger.info("⚡ اسکن خودکار...")
    r = await quick_scan()
    if r:
        try: await ctx.bot.send_message(ADMIN_CHAT_ID, r, parse_mode='HTML')
        except Exception as e: logger.error(f"Send: {e}")

async def auto_check(ctx):
    logger.info("🔄 چک پوزیشن‌ها...")
    for p in await get_active_positions():
        msg, st = await check_exit(p)
        if st == "EXIT" and msg:
            try: await ctx.bot.send_message(ADMIN_CHAT_ID, msg, parse_mode='HTML')
            except: pass
            await update_position_status(p['coin'], 'closed')

def err_handler(update, context):
    logger.error(f"Bot error: {context.error}")

# ==========================================
# 🚀 Main
# ==========================================
def main():
    _init_db()

    try:
        app = Flask(__name__)
        @app.route('/')
        def h(): return "OK 🔥"
        threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000, use_reloader=False),
                        daemon=True).start()
        logger.info("✅ Health OK")
    except Exception as e:
        logger.warning(f"Health: {e}")

    logger.info("🔥 v10.2 شروع...")
    logger.info(f"⏰ اسکن: {SCAN_INTERVAL} دقیقه | چک: {CHECK_INTERVAL} دقیقه")

    bot = Application.builder().token(TELEGRAM_TOKEN).build()
    bot.add_handler(CommandHandler("start", cmd_start))
    bot.add_handler(CommandHandler("help", cmd_start))
    bot.add_handler(CommandHandler("scan", cmd_scan))
    bot.add_handler(CommandHandler("positions", cmd_pos))
    bot.add_handler(CommandHandler("backtest", cmd_bt))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    bot.add_error_handler(err_handler)

    bot.job_queue.run_repeating(auto_scan, SCAN_INTERVAL * 60, first=10)
    bot.job_queue.run_repeating(auto_check, CHECK_INTERVAL * 60, first=30)

    logger.info("✅ آماده")
    bot.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()