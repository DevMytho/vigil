"""
Generates a synthetic dataset that mimics the schema of the Kaggle
"Credit Card Fraud Detection" dataset: Time, Amount, V1..V28 (PCA components),
and a Class label (0 = normal, 1 = fraud).

Why this exists: the real Kaggle dataset requires a kaggle.com login to
download. This script lets you build and test the ENTIRE pipeline
(model -> API -> Kafka -> dashboard) right now. When you're ready, download
the real dataset from:
    https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud
and drop it in this folder as `creditcard.csv` -- every downstream script
reads that exact filename and schema, so nothing else changes.

Run:
    python data/generate_synthetic_data.py
"""

import numpy as np
import pandas as pd

RNG = np.random.default_rng(42)

N_NORMAL = 20000
N_FRAUD = 40          # keep it heavily imbalanced, like the real dataset (~0.17%)
N_FEATURES = 28        # V1..V28


def generate():
    # --- Normal transactions ---
    normal_features = RNG.normal(loc=0.0, scale=1.0, size=(N_NORMAL, N_FEATURES))
    normal_amount = np.abs(RNG.normal(loc=60, scale=40, size=N_NORMAL))
    normal_time = np.sort(RNG.integers(0, 172800, size=N_NORMAL))  # 2 days, in seconds

    # --- Fraudulent transactions: shifted distribution, different amount profile ---
    fraud_features = RNG.normal(loc=2.5, scale=1.5, size=(N_FRAUD, N_FEATURES))
    fraud_amount = np.abs(RNG.normal(loc=400, scale=250, size=N_FRAUD))
    fraud_time = np.sort(RNG.integers(0, 172800, size=N_FRAUD))

    normal_df = pd.DataFrame(normal_features, columns=[f"V{i}" for i in range(1, N_FEATURES + 1)])
    normal_df["Time"] = normal_time
    normal_df["Amount"] = normal_amount
    normal_df["Class"] = 0

    fraud_df = pd.DataFrame(fraud_features, columns=[f"V{i}" for i in range(1, N_FEATURES + 1)])
    fraud_df["Time"] = fraud_time
    fraud_df["Amount"] = fraud_amount
    fraud_df["Class"] = 1

    df = pd.concat([normal_df, fraud_df], ignore_index=True)
    df = df.sample(frac=1.0, random_state=42).reset_index(drop=True)  # shuffle

    cols = ["Time"] + [f"V{i}" for i in range(1, N_FEATURES + 1)] + ["Amount", "Class"]
    df = df[cols]
    return df


if __name__ == "__main__":
    df = generate()
    out_path = "data/creditcard.csv"
    df.to_csv(out_path, index=False)
    print(f"Wrote {len(df)} rows ({df['Class'].sum()} fraud) to {out_path}")
    print(f"Fraud rate: {df['Class'].mean():.4%}")
