"""
Stage 4a: Producer.

Reads the dataset row by row and publishes each row as a JSON message to
the `transactions` Kafka topic, with a small delay between messages. This
is what simulates "live" transactions arriving over time -- Kafka doesn't
care that the source is actually a static CSV; it just sees a stream of
messages arriving.

Run (with Kafka already up via `docker compose up`):
    python streaming/producer.py
"""

import json
import time

import pandas as pd
from kafka import KafkaProducer

BOOTSTRAP_SERVERS = "localhost:9092"
TOPIC = "transactions"
DELAY_SECONDS = 0.2  # simulated time between transactions arriving


def main():
    producer = KafkaProducer(
        bootstrap_servers=BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )

    df = pd.read_csv("data/creditcard.csv")
    # Shuffle so fraud rows are scattered through the stream, like reality,
    # rather than clustered wherever they happened to sort in the CSV.
    df = df.sample(frac=1.0, random_state=7).reset_index(drop=True)

    print(f"Publishing {len(df)} transactions to topic '{TOPIC}'...")
    for i, row in df.iterrows():
        record = row.to_dict()
        # Keep the true label attached for the dashboard/consumer to compare
        # against the model's prediction -- in a REAL production system you
        # would NOT have this label available at scoring time; it's only
        # here so we can measure how well the model does on the live feed.
        producer.send(TOPIC, value=record)

        if i % 500 == 0:
            print(f"  published {i}/{len(df)}")
        time.sleep(DELAY_SECONDS)

    producer.flush()
    print("Done publishing.")


if __name__ == "__main__":
    main()
