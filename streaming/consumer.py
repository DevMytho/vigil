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

Reliability semantics (why the code below looks the way it does):

  RETRY -- a flaky API (restart, timeout, 5xx) must not drop
     transactions. Calls are retried with capped exponential backoff +
     jitter. 4xx responses that indicate a malformed payload (e.g. 422
     missing features) are NOT retried -- retrying cannot fix bad data.

  DEAD LETTER -- messages that still fail after MAX_ATTEMPTS, or that are
     unparseable, are published to the `transactions.dlq` topic with the
     error and the original payload attached instead of blocking the
     stream forever. They are also mirrored into the `dead_letters`
     SQLite table for visibility. If the DLQ publish itself fails, the
     offset is NOT committed and the consumer stops (it never advances
     past an undurable message), so the message is redelivered on the
     next run rather than lost.

  IDEMPOTENT CONSUMPTION -- Kafka gives at-least-once delivery: after a
     crash the same message can arrive again (we commit offsets manually,
     AFTER the work is durable). Every message is keyed by its Kafka
     identity (topic-partition-offset) in `processed_messages`, whose
     primary key makes the claim INSERT OR IGNORE. The claim and the
     result row are written in ONE SQLite transaction, so a replay either
     re-does the whole message (nothing was persisted) or is recognised as
     already processed (claim exists) and skipped. Net effect: one scored
     row per Kafka message, even under redelivery.

  FAIL LOUDLY -- offsets are per partition, so committing a later
     message's offset implicitly skips an earlier unprocessed one. Any
     unexpected error in the DB/commit path therefore exits the consumer
     instead of "logging and moving on"; the uncommitted message is
     redelivered on restart. Bad *payloads* don't crash the consumer --
     they go to the DLQ.

Run (with Kafka up AND the API running on :8000):
    python streaming/consumer.py
"""

import json
import random
import sqlite3
import time

import requests
from kafka import KafkaConsumer, KafkaProducer, TopicPartition
from kafka.errors import KafkaError
from kafka.structs import OffsetAndMetadata

BOOTSTRAP_SERVERS = "localhost:9092"
TOPIC = "transactions"
DLQ_TOPIC = "transactions.dlq"
API_URL = "http://localhost:8000/score"
DB_PATH = "dashboard/transactions.db"

MAX_ATTEMPTS = 5          # API attempts before the message goes to the DLQ
RETRY_BASE_DELAY = 0.5     # seconds; doubles each attempt
RETRY_MAX_DELAY = 8.0      # backoff cap
API_TIMEOUT = 5            # seconds per HTTP call
# Transient HTTP statuses worth retrying; any other non-2xx is a bad
# payload and goes straight to the DLQ (retrying cannot fix bad data).
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class PermanentMessageError(Exception):
    """Payload can never be scored (bad JSON, missing features, ...)."""


class RetryExhaustedError(Exception):
    """Transient failures persisted past MAX_ATTEMPTS."""


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
    # Idempotency ledger: one row per Kafka message ever fully handled.
    # dedupe_key = topic-partition-offset, the message's Kafka identity.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_messages (
            dedupe_key TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            partition INTEGER NOT NULL,
            kafka_offset INTEGER NOT NULL,
            first_seen_at REAL NOT NULL
        )
        """
    )
    # Parked messages, for inspection/replay. Mirrors what went to the DLQ.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dead_letters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dead_at REAL,
            topic TEXT,
            partition INTEGER,
            kafka_offset INTEGER,
            error TEXT,
            payload TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scored_pred ON scored_transactions(predicted_fraud)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scored_true ON scored_transactions(true_label)"
    )
    conn.commit()
    return conn


def score_with_retry(record: dict) -> dict:
    """POST to the API, retrying transient failures with backoff + jitter.

    A 200 whose body isn't the expected {is_fraud, anomaly_score} shape is
    treated as transient too (truncated/proxied response) -- it either
    comes back sane within MAX_ATTEMPTS or lands in the DLQ.
    """
    delay = RETRY_BASE_DELAY
    last_error = "no attempt made"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(API_URL, json=record, timeout=API_TIMEOUT)
        except requests.RequestException as e:
            last_error = f"{type(e).__name__}: {e}"
        else:
            if resp.status_code in RETRYABLE_STATUS:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            elif resp.status_code != 200:
                # e.g. 422 missing features: retrying cannot fix bad data.
                raise PermanentMessageError(
                    f"HTTP {resp.status_code}: {resp.text[:200]}"
                )
            else:
                try:
                    data = resp.json()
                except ValueError:
                    last_error = "API returned 200 with a non-JSON body"
                else:
                    if isinstance(data, dict) and "is_fraud" in data and "anomaly_score" in data:
                        return data
                    last_error = f"API response missing fields: {str(data)[:120]}"

        if attempt < MAX_ATTEMPTS:
            sleep_for = min(delay, RETRY_MAX_DELAY) * (0.5 + random.random())
            print(f"  API attempt {attempt}/{MAX_ATTEMPTS} failed ({last_error}); "
                  f"retrying in {sleep_for:.1f}s")
            time.sleep(sleep_for)
            delay *= 2

    raise RetryExhaustedError(last_error)


def send_to_dlq(producer: KafkaProducer, record, error: str, message) -> bool:
    """Publish a failed message to the DLQ topic. True only if durable."""
    payload = {
        "error": error,
        "failed_at": time.time(),
        "source_topic": message.topic,
        "source_partition": message.partition,
        "source_offset": message.offset,
        "record": record,
    }
    try:
        future = producer.send(DLQ_TOPIC, value=json.dumps(payload, default=str))
        future.get(timeout=10)  # synchronous: we must KNOW it landed
        return True
    except KafkaError as e:
        print(f"  DLQ publish failed: {e}")
        return False


def _publish_to_dlq(producer, record, error: str, message) -> bool:
    """send_to_dlq that never raises: any failure means 'not durable yet'."""
    try:
        return send_to_dlq(producer, record, error, message)
    except Exception as e:  # serializer bug, closed producer, ...
        print(f"  DLQ publish raised {e!r}")
        return False


def handle_message(conn, dlq_producer, msg) -> str:
    """Process one message. Returns one of:
    'scored' | 'duplicate' | 'dead-lettered' | 'deferred' (not durable yet).
    """
    dedupe_key = f"{msg.topic}-{msg.partition}-{msg.offset}"

    # Fast path: a replay of something already handled. The authoritative
    # gate is the INSERT OR IGNORE inside the transaction below; this just
    # avoids re-calling the API for obvious replays.
    if conn.execute(
        "SELECT 1 FROM processed_messages WHERE dedupe_key = ?", (dedupe_key,)
    ).fetchone():
        return "duplicate"

    true_label = None
    raw_text = None
    record = None
    try:
        raw_text = msg.value.decode("utf-8")
        record = json.loads(raw_text)
        if not isinstance(record, dict):
            raise PermanentMessageError("payload is not a JSON object")
        record = dict(record)
        true_label = record.pop("Class", None)
        result = score_with_retry(record)
    except PermanentMessageError as e:
        error = str(e)
        if not isinstance(record, dict):
            record = raw_text[:2000]  # keep the original bytes in the DLQ
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as e:
        error = f"unparseable payload: {e}"
        record = raw_text[:2000] if raw_text else None
    except RetryExhaustedError as e:
        error = f"gave up after {MAX_ATTEMPTS} attempts: {e}"
        # record is a dict here; fall through to the dead-letter path
    except Exception as e:  # unexpected scoring bug: park it, keep the stream alive
        error = f"unexpected error: {e!r}"
        record = record if isinstance(record, dict) else (raw_text[:2000] if raw_text else None)
    else:
        # Success: claim + result in ONE transaction. If the claim INSERT
        # hits an existing key (concurrent/replay duplicate), we roll back
        # so exactly one result row exists per Kafka message.
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO processed_messages
                    (dedupe_key, topic, partition, kafka_offset, first_seen_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (dedupe_key, msg.topic, msg.partition, msg.offset, time.time()),
            )
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                return "duplicate"
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
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        if result["is_fraud"]:
            flag = "FLAGGED" if true_label == 1 else "FLAGGED (false positive)"
            print(f"  [{flag}] amount={record.get('Amount'):.2f} "
                  f"score={result['anomaly_score']:.4f}")
        return "scored"

    # Dead-letter path (poison payload, permanent 4xx, or retries exhausted).
    # Publish to Kafka DLQ FIRST, then record claim + failure together; if
    # the publish fails we persist nothing and do not commit the offset, so
    # the broker redelivers and we try again later instead of dropping it.
    if not _publish_to_dlq(dlq_producer, record, error, msg):
        print(f"  could not dead-letter offset {msg.offset}; will redeliver")
        return "deferred"

    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO processed_messages
                (dedupe_key, topic, partition, kafka_offset, first_seen_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (dedupe_key, msg.topic, msg.partition, msg.offset, time.time()),
        )
        conn.execute(
            """
            INSERT INTO dead_letters
                (dead_at, topic, partition, kafka_offset, error, payload)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (time.time(), msg.topic, msg.partition, msg.offset, error,
             json.dumps(record, default=str)),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    print(f"  [DEAD-LETTERED] offset {msg.offset}: {error}")
    return "dead-lettered"


def commit_next_offset(consumer, message):
    """Commit message.offset + 1 for just this message's partition.

    kafka-python 2.x and 3.x disagree on the shape of commit(): 2.x takes a
    list of TopicPartition whose `.offset` field carries the value, 3.x
    (whose TopicPartition lost that field) takes a {tp: OffsetAndMetadata}
    dict. requirements.txt leaves kafka-python unpinned, so handle both.
    """
    tp = TopicPartition(message.topic, message.partition)
    if hasattr(tp, "offset"):  # kafka-python 2.x
        tp.offset = message.offset + 1
        consumer.commit(offsets=[tp])
    else:  # kafka-python 3.x
        consumer.commit(offsets={tp: OffsetAndMetadata(message.offset + 1)})


def main():
    conn = init_db()
    consumer = KafkaConsumer(
        TOPIC,
        bootstrap_servers=BOOTSTRAP_SERVERS,
        auto_offset_reset="earliest",
        group_id="fraud-scoring-consumer",
        enable_auto_commit=False,  # offsets committed manually, after durability
    )
    dlq_producer = KafkaProducer(
        bootstrap_servers=BOOTSTRAP_SERVERS,
        value_serializer=lambda v: v.encode("utf-8"),
    )

    print(f"Listening on topic '{TOPIC}' (DLQ: '{DLQ_TOPIC}')...")
    counts = {"scored": 0, "duplicate": 0, "dead-lettered": 0, "deferred": 0}

    try:
        for message in consumer:
            # No catch-all here on purpose: anything unexpected (a DB write
            # failure, a commit failure) propagates, the consumer exits, and
            # NOTHING for this offset was committed -- so a restart redelivers
            # it. Silently moving on would be worse: committing a LATER offset
            # for the same partition implicitly skips this message forever.
            status = handle_message(conn, dlq_producer, message)
            counts[status] += 1

            if status == "deferred":
                # Not durable: stop here instead of advancing past it. Kafka
                # will redeliver this message (and the rest) after a restart.
                print("  halting: message not durable yet; offsets left uncommitted")
                break

            # Commit ONLY this message's next offset, after the durable write.
            # That is what permits redelivery; the processed_messages ledger
            # makes that redelivery harmless.
            commit_next_offset(consumer, message)
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        print(f"Session totals: {counts}")
        consumer.close()
        dlq_producer.close()
        conn.close()


if __name__ == "__main__":
    main()
