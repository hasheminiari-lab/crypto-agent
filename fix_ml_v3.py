"""
Crypto-Agent v15 - Final ML Fix v3
بازآموزی با تنظیمات بهینه + Synthetic Data
"""
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
import pickle
from datetime import datetime

try:
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import StandardScaler
    from xgboost import XGBClassifier
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
    HAS_ML = True
except ImportError:
    HAS_ML = False

DB_PATH = Path(__file__).parent / "bot.db"
MODEL_PATH = Path(__file__).parent / "ml_model.pkl"


def load_data():
    """بارگذاری داده‌ها"""
    conn = sqlite3.connect(DB_PATH)
    
    query = """
        SELECT 
            z_score, volume_mult, rsi, result_24h,
            was_pump
        FROM signal_history
        WHERE result_24h IS NOT NULL
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    print(f"📊 Loaded {len(df)} samples")
    print(f"   Pumps: {df['was_pump'].sum()} ({df['was_pump'].mean()*100:.1f}%)")
    
    return df


def generate_synthetic_data(df, n_samples=200):
    """تولید داده‌های مصنوعی برای بهبود آموزش"""
    print(f"\n🔄 Generating {n_samples} synthetic samples...")
    
    synthetic = []
    
    # نمونه‌های مثبت (پامپ)
    pos_samples = df[df['was_pump'] == 1]
    if len(pos_samples) > 0:
        for _ in range(n_samples // 2):
            sample = pos_samples.sample(1).iloc[0]
            # اضافه کردن noise
            new_sample = {
                'z_score': sample['z_score'] + np.random.normal(0, 0.1),
                'volume_mult': sample['volume_mult'] + np.random.normal(0, 0.1),
                'rsi': sample['rsi'] + np.random.normal(0, 5),
                'result_24h': sample['result_24h'] + np.random.normal(0, 2),
                'was_pump': 1
            }
            synthetic.append(new_sample)
    
    # نمونه‌های منفی (غیر پامپ)
    neg_samples = df[df['was_pump'] == 0]
    if len(neg_samples) > 0:
        for _ in range(n_samples // 2):
            sample = neg_samples.sample(1).iloc[0]
            new_sample = {
                'z_score': sample['z_score'] + np.random.normal(0, 0.1),
                'volume_mult': sample['volume_mult'] + np.random.normal(0, 0.1),
                'rsi': sample['rsi'] + np.random.normal(0, 5),
                'result_24h': sample['result_24h'] + np.random.normal(0, 2),
                'was_pump': 0
            }
            synthetic.append(new_sample)
    
    synthetic_df = pd.DataFrame(synthetic)
    print(f"   Generated {len(synthetic_df)} synthetic samples")
    
    return synthetic_df


def train_model_v3(df):
    """آموزش مدل نسخه 3"""
    if not HAS_ML:
        return None
    
    # تولید داده‌های مصنوعی
    synthetic_df = generate_synthetic_data(df, n_samples=200)
    
    # ترکیب داده‌های واقعی و مصنوعی
    df_combined = pd.concat([df, synthetic_df], ignore_index=True)
    
    print(f"\n✅ Total samples: {len(df_combined)}")
    print(f"   Pumps: {df_combined['was_pump'].sum()} ({df_combined['was_pump'].mean()*100:.1f}%)")
    
    # Features
    feature_cols = ['z_score', 'volume_mult', 'rsi', 'result_24h']
    df_clean = df_combined.dropna(subset=feature_cols + ['was_pump'])
    
    X = df_clean[feature_cols].values
    y = df_clean['was_pump'].values
    
    # Split
    split_idx = int(len(X) * 0.8)
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]
    
    # Scale
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    
    # ✅ تنظیمات بهینه v3
    n_pos = (y_train == 1).sum()
    n_neg = (y_train == 0).sum()
    
    # ✅ کاهش بیشتر scale_pos_weight
    scale_pos_weight = min(2.0, n_neg / n_pos if n_pos > 0 else 1.0)
    
    print(f"\n⚖️ Class Balance:")
    print(f"   Positive: {n_pos}")
    print(f"   Negative: {n_neg}")
    print(f"   Scale weight: {scale_pos_weight:.2f}")
    
    # ✅ آموزش با تنظیمات بهتر
    print("\n🌲 Training XGBoost v3...")
    model = XGBClassifier(
        n_estimators=100,          # ✅ افزایش از 50 به 100
        max_depth=3,               # ✅ افزایش از 2 به 3
        learning_rate=0.1,         # ✅ کاهش از 0.2 به 0.1
        scale_pos_weight=scale_pos_weight,
        random_state=42,
        eval_metric='logloss',
        use_label_encoder=False,
        subsample=0.9,             # ✅ افزایش
        colsample_bytree=0.9,      # ✅ افزایش
        min_child_weight=2,        # ✅ کاهش
        gamma=0.1,                 # ✅ اضافه شده
        reg_alpha=0.1,             # ✅ اضافه شده
        reg_lambda=1.0,            # ✅ اضافه شده
    )
    
    model.fit(X_train_scaled, y_train)
    
    # Evaluate
    y_pred = model.predict(X_test_scaled)
    y_prob = model.predict_proba(X_test_scaled)[:, 1]
    
    metrics = {
        'accuracy': float(accuracy_score(y_test, y_pred)),
        'precision': float(precision_score(y_test, y_pred, zero_division=0)),
        'recall': float(recall_score(y_test, y_pred, zero_division=0)),
        'f1': float(f1_score(y_test, y_pred, zero_division=0)),
    }
    
    print("\n📊 Model Metrics:")
    for k, v in metrics.items():
        print(f"   {k}: {v:.3f}")
    
    # Test predictions
    print("\n🧪 Testing Predictions:")
    test_cases = [
        ("Good Signal", 0.5, 1.2, 50, 10),
        ("Bad Signal", 2.0, 3.0, 80, -20),
        ("Neutral", 1.0, 1.5, 50, 0),
        ("Strong Pump", 0.3, 0.8, 40, 25),
    ]
    
    for name, z, vol, rsi, change in test_cases:
        features = np.array([[z, vol, rsi, change]])
        features_scaled = scaler.transform(features)
        prob = model.predict_proba(features_scaled)[0][1]
        print(f"   {name}: Prob = {prob*100:.1f}%")
    
    # Save
    model_data = {
        'model': model,
        'scaler': scaler,
        'metrics': metrics,
        'feature_names': feature_cols,
        'train_date': datetime.now().isoformat(),
    }
    
    with open(MODEL_PATH, 'wb') as f:
        pickle.dump(model_data, f)
    
    print(f"\n✅ Model saved to {MODEL_PATH}")
    return model_data


def main():
    print("=" * 70)
    print("🎯 CRYPTO-AGENT v15 - FINAL ML FIX v3")
    print("=" * 70)
    
    df = load_data()
    
    if len(df) < 10:
        print("❌ Not enough data (need at least 10 samples)")
        return
    
    train_model_v3(df)
    
    print("\n" + "=" * 70)
    print("✅ Training complete!")
    print("💡 Restart scanner_v15.py to use new model")
    print("=" * 70)


if __name__ == "__main__":
    main()