"""
Stage 4b: Consumer.

Subscribes to the `transactions` topic. For every message that arrives:
  1. Strip the true label (Class) before sending to the API -- the model
     must not see the answer. We keep it locally only to measure accuracy
     on the live feed.
  2. Call the FastAPI /score endpoint (deliberately going over HTTP, not
     calling the model function directly, to keep the serving layer
     genuinely decoupled from the streaming layer).
  3. Write the result to SQLite so the dashboard can display it.

Run (with Kafka up AND the API running on :8000):
    python streaming/consumer.py
"""

import json
import sqlite3
import time

import requests
from kafka import KafkaConsumer

BOOTSTRAP_SERVERS = "localhost:9092"
TOPIC = "transactions"
API_URL = "http://localhost:8000/score"
DB_PATH = "dashboard/transactions.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scored_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at REAL,
            time_field REAL,
            amount REAL,
            true_label INTEGER,
            predicted_fraud INTEGER,
            anomaly_score REAL
        )
        """
    )
    conn.commit()
    return conn


def main():
    conn = init_db()
    consumer = KafkaConsumer(
        TOPIC,
        bootstrap_servers=BOOTSTRAP_SERVERS,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="earliest",
        group_id="fraud-scoring-consumer",
    )

    print(f"Listening on topic '{TOPIC}'...")
    for message in consumer:
        record = dict(message.value)
        true_label = record.pop("Class", None)

        try:
            resp = requests.post(API_URL, json=record, timeout=5)
            resp.raise_for_status()
            result = resp.json()
        except requests.RequestException as e:
            print(f"  API call failed: {e}")
            continue

        conn.execute(
            """
            INSERT INTO scored_transactions
                (received_at, time_field, amount, true_label, predicted_fraud, anomaly_score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                record.get("Time"),
                record.get("Amount"),
                true_label,
                int(result["is_fraud"]),
                result["anomaly_score"],
            ),
        )
        conn.commit()

        if result["is_fraud"]:
            flag = "FLAGGED" if true_label == 1 else "FLAGGED (false positive)"
            print(f"  [{flag}] amount={record.get('Amount'):.2f} score={result['anomaly_score']:.4f}")


if __name__ == "__main__":
    main()
