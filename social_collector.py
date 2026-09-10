"""
Crypto-Agent v15 - Social Collector
جمع‌آوری Social Metrics از منابع رایگان
"""
import asyncio
import httpx
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)

# Fear & Greed Index API (رایگان)
FNG_API = "https://api.alternative.me/fng/"

async def get_fear_greed_index() -> Dict:
    """دریافت Fear & Greed Index"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(FNG_API)
            if resp.status_code == 200:
                data = resp.json().get('data', [{}])[0]
                return {
                    'value': int(data.get('value', 50)),
                    'classification': data.get('value_classification', 'Neutral'),
                    'timestamp': data.get('timestamp', ''),
                }
    except Exception as e:
        logger.debug(f"Fear & Greed error: {e}")
    
    return {
        'value': 50,
        'classification': 'Neutral',
        'timestamp': '',
    }

async def get_social_score(symbol: str) -> Dict:
    """
    دریافت Social Score برای یک کوین
    فعلاً بر اساس Fear & Greed Index
    بعداً می‌توان LunarCrush یا Twitter API اضافه کرد
    """
    fng = await get_fear_greed_index()
    
    # تبدیل Fear & Greed به Social Score
    # Extreme Fear (0-25) = فرصت خرید = امتیاز بالا
    # Extreme Greed (75-100) = خطر = امتیاز پایین
    
    fng_value = fng['value']
    
    if fng_value <= 25:
        social_score = 90  # Extreme Fear = فرصت عالی
    elif fng_value <= 40:
        social_score = 70  # Fear = فرصت خوب
    elif fng_value <= 60:
        social_score = 50  # Neutral = خنثی
    elif fng_value <= 75:
        social_score = 30  # Greed = احتیاط
    else:
        social_score = 10  # Extreme Greed = خطر
    
    return {
        'social_score': social_score,
        'fear_greed_index': fng_value,
        'fear_greed_class': fng['classification'],
    }

# تست
async def test():
    print("🧪 Testing Social Collector\n")
    
    fng = await get_fear_greed_index()
    print(f"Fear & Greed Index: {fng['value']} ({fng['classification']})")
    
    social = await get_social_score('BTC')
    print(f"Social Score: {social['social_score']}")
    print(f"Fear & Greed: {social['fear_greed_index']}")

if __name__ == "__main__":
    asyncio.run(test())