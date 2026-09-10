"""
پیش‌بینی پامپ با مدل آموزش‌دیده
"""
import pickle
import numpy as np
import pandas as pd
from pathlib import Path

MODEL_PATH = Path(__file__).parent / "pump_model.pkl"

FEATURES = [
    'z_1h', 'z_4h', 'vol_mult_1h', 'vol_mult_4h',
    'change_24h', 'rsi', 'macd_hist',
    'bollinger_pctb', 'atr_ratio',
    'volume_24h', 'liquidity', 'market_cap',
    'funding_rate', 'oi_change',
    'accumulation', 'breakout', 'price_acceleration',
    'tier_z'
]

class PumpPredictor:
    def __init__(self):
        self.model = None
        self.scaler = None
        self.model_name = None
        self.load()
    
    def load(self):
        if MODEL_PATH.exists():
            try:
                with open(MODEL_PATH, 'rb') as f:
                    data = pickle.load(f)
                    self.model = data['model']
                    self.scaler = data['scaler']
                    self.model_name = data.get('model_name', 'Unknown')
                    print(f"✅ مدل بارگذاری شد: {self.model_name} (AUC: {data.get('auc', 0):.3f})")
            except Exception as e:
                print(f"⚠️ خطا در بارگذاری مدل: {e}")
        else:
            print("️ مدل یافت نشد")
    
    def predict(self, coin_features: dict) -> dict:
        if self.model is None:
            return {'probability': 0.5, 'decision': 'no_model', 'confidence': 'low'}
        
        try:
            # ✅ استفاده از DataFrame با feature names
            X = pd.DataFrame([[coin_features.get(f, 0) for f in FEATURES]], columns=FEATURES)
            X_scaled = self.scaler.transform(X)
            
            prob = float(self.model.predict_proba(X_scaled)[0][1])
            pred = int(self.model.predict(X_scaled)[0])
            
            if prob > 0.75:
                decision = 'strong_buy'
                confidence = 'high'
            elif prob > 0.6:
                decision = 'buy'
                confidence = 'medium'
            elif prob > 0.45:
                decision = 'watch'
                confidence = 'medium'
            else:
                decision = 'skip'
                confidence = 'low'
            
            return {
                'probability': prob,
                'prediction': pred,
                'decision': decision,
                'confidence': confidence,
                'model': self.model_name
            }
        except Exception as e:
            return {'probability': 0.5, 'decision': 'error', 'error': str(e)}


if __name__ == "__main__":
    predictor = PumpPredictor()
    
    sample = {
        'z_1h': 2.5, 'z_4h': 2.0,
        'vol_mult_1h': 3.0, 'vol_mult_4h': 2.5,
        'change_24h': 5.0, 'rsi': 55,
        'macd_hist': 0.001, 'bollinger_pctb': 0.7,
        'atr_ratio': 0.02, 'volume_24h': 1000000,
        'liquidity': 500000, 'market_cap': 20000000,
        'funding_rate': -0.005, 'oi_change': 15,
        'accumulation': 1, 'breakout': 0,
        'price_acceleration': 0, 'tier_z': 1.5
    }
    
    result = predictor.predict(sample)
    print(f"\nنتیجه: {result}")