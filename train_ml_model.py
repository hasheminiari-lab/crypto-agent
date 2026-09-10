"""
Crypto-Agent v15 - ML Model Training
آموزش مدل XGBoost با داده‌های واقعی
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
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score
    HAS_ML = True
except ImportError:
    HAS_ML = False
    print("❌ ML libraries not installed. Run: pip install scikit-learn xgboost")

DB_PATH = Path(__file__).parent / "bot.db"
MODEL_PATH = Path(__file__).parent / "ml_model.pkl"


def load_training_data():
    """بارگذاری داده‌های آموزشی از دیتابیس"""
    if not DB_PATH.exists():
        print(f"❌ Database not found: {DB_PATH}")
        return None
    
    conn = sqlite3.connect(DB_PATH)
    
    # بارگذاری signal_history با features
    query = """
        SELECT 
            sh.id,
            sh.coin,
            sh.z_score,
            sh.volume_mult,
            sh.rsi,
            sh.score,
            sh.pattern,
            sh.result_24h,
            sh.was_pump,
            sh.entry_time
        FROM signal_history sh
        WHERE sh.result_24h IS NOT NULL
        ORDER BY sh.entry_time ASC
    """
    
    try:
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        print(f"📊 Loaded {len(df)} samples")
        
        if len(df) == 0:
            print("❌ No labeled data found")
            return None
        
        # آمار داده‌ها
        pumps = df['was_pump'].sum()
        total = len(df)
        print(f"   Pumps: {pumps} ({pumps/total*100:.1f}%)")
        print(f"   Non-pumps: {total - pumps} ({(total-pumps)/total*100:.1f}%)")
        
        # بررسی features
        print(f"\n📈 Feature Statistics:")
        for col in ['z_score', 'volume_mult', 'rsi']:
            if col in df.columns:
                print(f"   {col}: mean={df[col].mean():.2f}, std={df[col].std():.2f}")
        
        return df
        
    except Exception as e:
        print(f"❌ Error loading data: {e}")
        conn.close()
        return None


def prepare_features(df):
    """آماده‌سازی features"""
    # انتخاب features
    feature_cols = ['z_score', 'volume_mult', 'rsi']
    
    # اضافه کردن change_24h اگر موجود باشد
    if 'result_24h' in df.columns:
        feature_cols.append('result_24h')
    
    # حذف NaN
    df_clean = df.dropna(subset=feature_cols + ['was_pump'])
    
    X = df_clean[feature_cols].values
    y = df_clean['was_pump'].values
    
    print(f"\n✅ Prepared {len(X)} samples with {len(feature_cols)} features")
    print(f"   Features: {feature_cols}")
    
    return X, y, feature_cols


def train_model(X, y, feature_names):
    """آموزش مدل XGBoost"""
    if not HAS_ML:
        print("❌ ML libraries not available")
        return None
    
    # Split data (temporal split - no shuffle)
    split_idx = int(len(X) * 0.8)
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]
    
    print(f"\n📊 Data Split:")
    print(f"   Train: {len(X_train)} samples")
    print(f"   Test: {len(X_test)} samples")
    
    # Scale features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    
    # Calculate class weight
    n_pos = (y_train == 1).sum()
    n_neg = (y_train == 0).sum()
    scale_pos_weight = n_neg / n_pos if n_pos > 0 else 1.0
    
    print(f"\n⚖️ Class Balance:")
    print(f"   Positive: {n_pos}")
    print(f"   Negative: {n_neg}")
    print(f"   Scale weight: {scale_pos_weight:.2f}")
    
    # Train XGBoost
    print("\n🌲 Training XGBoost...")
    model = XGBClassifier(
        n_estimators=100,
        max_depth=3,
        learning_rate=0.1,
        scale_pos_weight=scale_pos_weight,
        random_state=42,
        eval_metric='logloss',
        use_label_encoder=False
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
        'auc': float(roc_auc_score(y_test, y_prob)) if len(np.unique(y_test)) > 1 else 0.0
    }
    
    print("\n📊 Model Metrics:")
    for k, v in metrics.items():
        print(f"   {k}: {v:.3f}")
    
    # Feature importance
    print("\n🎯 Feature Importance:")
    importances = model.feature_importances_
    for name, imp in sorted(zip(feature_names, importances), key=lambda x: x[1], reverse=True):
        print(f"   {name}: {imp:.3f}")
    
    return {
        'model': model,
        'scaler': scaler,
        'metrics': metrics,
        'feature_names': feature_names,
        'train_date': datetime.now().isoformat(),
        'train_size': len(X_train),
        'test_size': len(X_test)
    }


def save_model(model_data):
    """ذخیره مدل"""
    if model_data is None:
        print("❌ No model to save")
        return False
    
    try:
        with open(MODEL_PATH, 'wb') as f:
            pickle.dump(model_data, f)
        
        print(f"\n✅ Model saved to {MODEL_PATH}")
        print(f"   Size: {MODEL_PATH.stat().st_size / 1024:.1f} KB")
        return True
        
    except Exception as e:
        print(f"❌ Error saving model: {e}")
        return False


def main():
    """اجرای اصلی"""
    print("=" * 70)
    print("🎯 CRYPTO-AGENT v15 - ML MODEL TRAINING")
    print("=" * 70)
    
    # بارگذاری داده‌ها
    df = load_training_data()
    if df is None:
        return
    
    # آماده‌سازی features
    X, y, feature_names = prepare_features(df)
    
    # آموزش مدل
    model_data = train_model(X, y, feature_names)
    
    # ذخیره مدل
    if save_model(model_data):
        print("\n" + "=" * 70)
        print("✅ Training complete!")
        print("💡 Next: Update scanner_v15.py to use this model")
        print("=" * 70)
    else:
        print("\n❌ Training failed")


if __name__ == "__main__":
    main()