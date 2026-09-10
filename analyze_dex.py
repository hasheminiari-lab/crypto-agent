"""
Crypto-Agent v15 - Analyze DEX Coins
بررسی چرا DEX coins در نتایج نیستند
"""
import asyncio
import httpx
import numpy as np
from scanner_v15 import (
    get_dex_coins, calc_z_scores, calc_v15_score, 
    is_valid_symbol, V15_RULES, MLPredictor
)

async def analyze_dex():
    print("=" * 70)
    print("🔍 ANALYZING DEX COINS")
    print("=" * 70)
    
    # جمع‌آوری DEX coins
    dex_coins = await get_dex_coins()
    print(f"\n🦄 Collected {len(dex_coins)} DEX coins\n")
    
    ml_predictor = MLPredictor()
    
    async with httpx.AsyncClient(timeout=300) as client:
        passed = 0
        failed = 0
        
        for coin in dex_coins[:20]:  # بررسی 20 کوین اول
            sym = coin['symbol']
            
            try:
                # محاسبه Z-Score
                zs = await calc_z_scores(sym, client)
                z4 = zs.get('4h', (0, 0))[0]
                m4 = zs.get('4h', (0, 0))[1]
                
                # محاسبه امتیاز
                pattern = 'Monitor' if z4 < 1.0 else 'Volume Spike'
                ml_prob = ml_predictor.predict(z4, m4, 50, coin.get('change', 0))
                
                score, passes = calc_v15_score(
                    z4, m4, pattern,
                    change=coin.get('change', 0),
                    ml_prob=ml_prob,
                    social_score=50
                )
                
                status = "✅ PASS" if passes else "❌ FAIL"
                if passes:
                    passed += 1
                else:
                    failed += 1
                
                print(f"{sym:10s} | Z: {z4:5.2f} | Vol: {m4:5.2f}x | "
                      f"Chg: {coin.get('change', 0):+6.1f}% | "
                      f"Score: {score:5.1f} | {status}")
                
                # اگر fail شد، دلیل را بررسی کن
                if not passes:
                    reasons = []
                    if z4 >= V15_RULES['max_z_score']:
                        reasons.append(f"Z>={V15_RULES['max_z_score']}")
                    if m4 >= V15_RULES['max_vol_mult']:
                        reasons.append(f"Vol>={V15_RULES['max_vol_mult']}")
                    if coin.get('change', 0) < V15_RULES['min_change']:
                        reasons.append(f"Chg<{V15_RULES['min_change']}")
                    if coin.get('change', 0) > V15_RULES['max_change']:
                        reasons.append(f"Chg>{V15_RULES['max_change']}")
                    if score < V15_RULES['min_score']:
                        reasons.append(f"Score<{V15_RULES['min_score']}")
                    
                    if reasons:
                        print(f"{'':10s} | Reasons: {', '.join(reasons)}")
                
            except Exception as e:
                print(f"{sym:10s} | ERROR: {e}")
            
            await asyncio.sleep(0.5)
        
        print(f"\n{'='*70}")
        print(f"📊 Summary: {passed} passed, {failed} failed")
        print(f"{'='*70}")

if __name__ == "__main__":
    asyncio.run(analyze_dex())