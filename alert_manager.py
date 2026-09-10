"""
Crypto-Agent v15 - Alert Manager
ارسال هشدارهای مهم به تلگرام
"""
import httpx
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))


async def send_alert(message: str, level: str = "INFO"):
    """ارسال هشدار به تلگرام"""
    if not TELEGRAM_TOKEN or not ADMIN_CHAT_ID:
        print("❌ Telegram not configured")
        return
    
    emojis = {
        'INFO': 'ℹ️',
        'WARNING': '⚠️',
        'ERROR': '❌',
        'SUCCESS': '✅',
    }
    
    emoji = emojis.get(level, 'ℹ️')
    full_message = f"{emoji} {message}"
    
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={
                    'chat_id': ADMIN_CHAT_ID,
                    'text': full_message
                },
                timeout=10
            )
    except Exception as e:
        print(f"❌ Alert error: {e}")


async def alert_scan_complete(total_coins, signals, dex_count):
    """هشدار تکمیل اسکن"""
    message = f"Scan Complete\n\n"
    message += f"📊 Coins: {total_coins}\n"
    message += f"🎯 Signals: {signals}\n"
    message += f"🦄 DEX: {dex_count}"
    
    await send_alert(message, 'SUCCESS')


async def alert_performance(win_rate, profit_factor):
    """هشدار عملکرد"""
    if win_rate < 50:
        message = f"Performance Alert\n\n"
        message += f"❌ Win Rate: {win_rate:.1f}%\n"
        message += f"⚠️ Below 50% threshold"
        await send_alert(message, 'WARNING')
    
    if profit_factor < 1.0:
        message = f"Performance Alert\n\n"
        message += f"❌ Profit Factor: {profit_factor:.2f}\n"
        message += f"⚠️ Below 1.0 threshold"
        await send_alert(message, 'WARNING')


async def alert_error(error_msg):
    """هشدار خطا"""
    message = f"Error Occurred\n\n{error_msg[:200]}"
    await send_alert(message, 'ERROR')


# تست
async def test():
    print("🧪 Testing Alert Manager\n")
    await send_alert("Test alert from Crypto-Agent v15", 'INFO')
    print("✅ Alert sent")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test())