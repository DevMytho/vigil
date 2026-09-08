"""
Stage 1: Data assessment + model training.

What this script does, in order:
  1. Load the CSV and report class imbalance (this number is WHY we use
     anomaly detection instead of plain supervised classification).
  2. Scale the `Amount` column (V1..V28 are already PCA-normalized; Amount
     is on a totally different scale and needs to match).
  3. Split into train/test -- critically, TRAIN ONLY ON NORMAL (Class==0)
     transactions. The model learns what "normal" looks like. Fraud rows
     are held out entirely from training and only used to evaluate.
  4. Train an Isolation Forest anomaly detector.
  5. Evaluate with precision/recall/F1 on the held-out mixed test set
     (accuracy is meaningless here -- a model that predicts "normal" for
     everything would still score >99% accuracy).
  6. Save the trained model + the fitted scaler to disk with joblib.

Run:
    python models/train_model.py
"""

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

DATA_PATH = "data/creditcard.csv"
MODEL_PATH = "models/isolation_forest.joblib"
SCALER_PATH = "models/scaler.joblib"


def load_and_assess(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)

    print("=== Data assessment ===")
    print(f"Total rows: {len(df)}")
    fraud_count = df["Class"].sum()
    fraud_rate = df["Class"].mean()
    print(f"Fraud rows: {fraud_count} ({fraud_rate:.4%})")
    print(f"Null values:\n{df.isnull().sum().sum()} total nulls")
    print(f"Amount stats -- normal vs fraud:")
    print(df.groupby("Class")["Amount"].describe()[["mean", "std", "min", "max"]])
    print()

    return df


def prepare_features(df: pd.DataFrame):
    feature_cols = [c for c in df.columns if c not in ("Class",)]

    scaler = StandardScaler()
    df = df.copy()
    df["Amount"] = scaler.fit_transform(df[["Amount"]])
    # Time isn't predictive of "normal-ness" in the same way -- drop it from
    # model features but keep it in the raw record for the API/dashboard to
    # display. (V1..V28 are already normalized by the original PCA step.)
    model_features = [c for c in feature_cols if c != "Time"]

    return df, model_features, scaler


def train_and_evaluate(df: pd.DataFrame, model_features: list[str]):
    normal_df = df[df["Class"] == 0]
    fraud_df = df[df["Class"] == 1]

    # Train ONLY on normal transactions.
    train_normal, test_normal = train_test_split(normal_df, test_size=0.3, random_state=42)
    # Test set is a mix: held-out normal + ALL fraud (fraud never seen in training).
    test_df = pd.concat([test_normal, fraud_df], ignore_index=True)

    X_train = train_normal[model_features].values
    X_test = test_df[model_features].values
    y_test = test_df["Class"].values

    print("=== Training Isolation Forest ===")
    # contamination = expected proportion of anomalies; we set it close to
    # the real-world fraud rate as a prior for the decision threshold.
    contamination = max(fraud_df.shape[0] / len(df), 0.001)
    model = IsolationForest(
        n_estimators=200,
        contamination=contamination,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train)

    # IsolationForest.predict returns 1 (normal) / -1 (anomaly).
    # Convert to our convention: 1 = fraud, 0 = normal, to match the Class column.
    raw_preds = model.predict(X_test)
    y_pred = np.where(raw_preds == -1, 1, 0)

    print("=== Evaluation on held-out test set ===")
    print(classification_report(y_test, y_pred, target_names=["normal", "fraud"]))
    print("Confusion matrix (rows=true, cols=predicted):")
    print(confusion_matrix(y_test, y_pred))

    return model


if __name__ == "__main__":
    df = load_and_assess(DATA_PATH)
    df, model_features, scaler = prepare_features(df)
    model = train_and_evaluate(df, model_features)

    joblib.dump(model, MODEL_PATH)
    joblib.dump({"scaler": scaler, "model_features": model_features}, SCALER_PATH)
    print(f"\nSaved model to {MODEL_PATH}")
    print(f"Saved scaler + feature list to {SCALER_PATH}")
