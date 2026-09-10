import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
import pickle
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score

DB_PATH = Path(__file__).parent / "bot.db"

def load_training_data():
    """بارگذاری داده‌های آموزشی"""
    conn = sqlite3.connect(DB_PATH)
    
    # بارگذاری signal_history
    query = """
        SELECT 
            z_score, volume_mult, rsi, change_24h,
            result_24h, was_pump
        FROM signal_history
        WHERE result_24h IS NOT NULL
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    print(f"📊 Loaded {len(df)} samples")
    print(f"   Pumps: {df['was_pump'].sum()} ({df['was_pump'].mean()*100:.1f}%)")
    
    return df

def train_model():
    """آموزش مدل XGBoost"""
    df = load_training_data()
    
    if len(df) < 30:
        print("❌ Not enough data (need at least 30 samples)")
        return
    
    # Features
    X = df[['z_score', 'volume_mult', 'rsi', 'change_24h']].values
    y = df['was_pump'].values
    
    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    
    # Scale
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    
    # Train
    print("\n🌲 Training XGBoost...")
    model = XGBClassifier(
        n_estimators=100,
        max_depth=3,
        learning_rate=0.1,
        scale_pos_weight=(y_train == 0).sum() / (y_train == 1).sum(),
        random_state=42
    )
    model.fit(X_train_scaled, y_train)
    
    # Evaluate
    y_pred = model.predict(X_test_scaled)
    y_prob = model.predict_proba(X_test_scaled)[:, 1]
    
    metrics = {
        'accuracy': accuracy_score(y_test, y_pred),
        'precision': precision_score(y_test, y_pred, zero_division=0),
        'recall': recall_score(y_test, y_pred, zero_division=0),
        'f1': f1_score(y_test, y_pred, zero_division=0),
        'auc': roc_auc_score(y_test, y_prob) if len(np.unique(y_test)) > 1 else 0
    }
    
    print("\n📊 Model Metrics:")
    for k, v in metrics.items():
        print(f"   {k}: {v:.3f}")
    
    # Save
    model_data = {
        'model': model,
        'scaler': scaler,
        'metrics': metrics,
        'feature_names': ['z_score', 'volume_mult', 'rsi', 'change_24h']
    }
    
    model_path = Path(__file__).parent / "ml_model.pkl"
    with open(model_path, 'wb') as f:
        pickle.dump(model_data, f)
    
    print(f"\n✅ Model saved to {model_path}")
    return model_data

if __name__ == "__main__":
    train_model()