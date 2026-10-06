# VIGIL

**V**igilant **I**solation-based **G**uard for **I**llicit **L**edger-activity

A streaming anomaly-detection system that scores credit card transactions
for fraud risk in real time, built to mirror the shape of production
fraud-scoring systems (e.g. Stripe Radar) at a scale you can run on a
laptop.

## Architecture

```
[CSV dataset] -> [Kafka Producer] -> [Kafka topic: transactions] -> [Kafka Consumer]
                                                                           |
                                                                           v
                                                            [FastAPI /score endpoint]
                                                              (Isolation Forest model)
                                                                           |
                                                                           v
                                                                [SQLite result store]
                                                                           |
                                                                           v
                                                              [Streamlit dashboard]
```

Messages that still fail after retries -- or that aren't parseable at
all -- are routed to the `transactions.dlq` dead-letter topic instead of
blocking the stream (see "Consumer reliability" below).


**Why anomaly detection, not supervised classification:** fraud is ~0.17%
of transactions. A model trained to predict "not fraud" for everything
would score >99% accuracy while being useless. Instead, the model is
trained ONLY on normal transactions and learns what "normal" looks like;
anything sufficiently different gets flagged. This also better reflects
reality: fraud patterns shift over time, so a system that recognizes
"deviation from normal" generalizes better than one memorizing known fraud
patterns.

**Why Kafka:** decouples data arrival from data processing. A card
network doesn't call a function when you swipe your card -- it publishes
an event that multiple independent systems consume (fraud scoring,
rewards, notifications). This project recreates that shape at toy scale.

## Design decisions I scoped OUT (and why)

- **lakeFS**: solves dataset versioning/reproducibility for data that
  changes over time across teams. This project has one static CSV -- there
  is nothing to version. Would matter if continuously retraining on new
  daily transaction data and needing audit trails for compliance.
- **AWS-hosted model (e.g. SageMaker)**: adds cost, IAM/account setup, and
  a dependency on a paid resource staying warm for demos -- none of which
  demonstrates the actual point of this project (anomaly detection +
  streaming architecture). `docker compose up` + a few scripts is a better
  reviewer experience than "here's my AWS console."
- **Metabase**: a BI/dashboarding tool, not a model server -- it visualizes
  data in a database, it doesn't run inference. Streamlit fills the
  dashboard role here directly against SQLite.

In production, these tools would matter: model versioning via MLflow,
data lineage via lakeFS, deployment behind a managed autoscaling endpoint.

## Setup

```bash
pip install -r requirements.txt
```

### 1. Get data

Either generate synthetic data to test the pipeline immediately:

```bash
python data/generate_synthetic_data.py
```

Or download the real dataset from
https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud and place it at
`data/creditcard.csv` (same schema, no code changes needed). Fastest
path to the real file, no browser or Kaggle credentials required:

```bash
pip install kagglehub
python -c "import kagglehub, pathlib, shutil; src = pathlib.Path(kagglehub.dataset_download('mlg-ulb/creditcardfraud')) / 'creditcard.csv'; shutil.copy(src, 'data/creditcard.csv')"
```

Sanity check: the real file has 284,807 rows and 492 fraud (0.1727%).
The synthetic generator writes 20,040 rows / 40 fraud to the same path,
so `wc -l data/creditcard.csv` is a quick way to tell which one you have.

### 2. Train the model

```bash
python models/train_model.py
```

Reports class imbalance, trains an Isolation Forest on normal transactions
only, evaluates precision/recall plus PR-AUC (average precision) on a
held-out mixed test set, and saves the model to
`models/isolation_forest.joblib`.

### 3. Start the API

```bash
uvicorn api.main:app --reload --port 8000
```

Test it:
```bash
curl -X POST http://localhost:8000/score \
  -H "Content-Type: application/json" \
  -d '{"Time": 1000, "Amount": 500, "V1": 2.1, "V2": 1.8, ...}'
```

### 4. Start Kafka

```bash
docker compose up -d
```

### 5. Run the producer and consumer (separate terminals)

```bash
python streaming/producer.py
python streaming/consumer.py
```

#### Consumer reliability

`streaming/consumer.py` treats the network and the API as hostile:

- **Retries** -- connection errors, timeouts, 5xx and 429 are retried up
  to 5 times with capped exponential backoff plus jitter. A 422 (payload
  missing model features) is *not* retried: bad data can't fix itself.
- **Dead letters** -- messages that still fail after all retries, or that
  aren't valid JSON, are published to the `transactions.dlq` topic with
  the error and the original payload, and mirrored into the
  `dead_letters` SQLite table for inspection/replay. If the DLQ publish
  itself fails, the offset is *not* committed, so the broker redelivers
  the message on the next run rather than dropping it.
- **Idempotent consumption** -- offsets are committed manually and only
  after the result is durable in SQLite, which means Kafka is allowed to
  redeliver (at-least-once) without corrupting counts. Every message is
  claimed by its topic-partition-offset key in `processed_messages`
  (a SQLite primary key), and the claim plus the result row are written
  in one transaction: a replayed message is recognised and skipped
  instead of being scored and stored twice.

Invariant the code maintains: `processed_messages = scored_transactions +
dead_letters`, always.

### 6. Watch the dashboard

```bash
streamlit run dashboard/app.py
```

## Results (real Kaggle dataset)

Numbers below are from the actual
[mlg-ulb/creditcardfraud](https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud)
dataset -- 284,807 transactions, 492 of them fraud (0.1727%) -- not from
the synthetic generator. Reproduce with `python models/train_model.py`.

Protocol: IsolationForest trained ONLY on a 70% split of normal
transactions, evaluated on the held-out 30% of normal plus all 492 fraud
rows (fraud never appears in training).

- **PR-AUC (average precision on the continuous anomaly score):
  0.2968**
- Random-classifier baseline on the same test set: **0.0057** (= fraud
  prevalence) -> **51.8x lift**
- Recall at the top-K scores an analyst could actually review:
  36.8% at K = #fraud (precision 36.8%), 51.8% at K = 2x#fraud,
  77.6% at K = 5x#fraud
- At the model's default `contamination` threshold: precision 0.51,
  recall 0.27 (133/492 fraud caught, 130 false positives on 85,295
  normal test rows)

Why PR-AUC is the headline: a single precision/recall pair is one point
on the curve and moves entirely with the decision threshold, while PR-AUC
integrates precision across every recall level. Threshold-free numbers
are the only honest way to compare models on a 0.17% base rate. Tuning
`contamination` slides along that curve; beating it structurally needs a
different model (supervised gradient boosting, autoencoder) rather than a
different threshold.

## Desktop app (vigil-desktop/)

A Tauri desktop wrapper that bundles the API as a sidecar binary. The
Python FastAPI service gets frozen into a standalone executable via
PyInstaller and run as a Tauri sidecar process. The React frontend talks
to it over `localhost` like any web app.

```bash
cd vigil-desktop
npm install
npm run tauri dev          # opens the Tauri window with live reload
```

To produce a platform-native installer:
```bash
# Full build: freezes Python API + builds Tauri
./scripts/build.sh

# Or just rebuild the Tauri app (reuse existing sidecar binary)
./scripts/build.sh --skip-py
```

See [`vigil-desktop/README.md`](vigil-desktop/README.md) for full
details on the desktop app architecture and CI/CD.

## Project structure

```
vigil/
├── docker-compose.yml       # Kafka + Zookeeper
├── requirements.txt
├── vigil-api.spec            # PyInstaller spec for sidecar build
├── data/
│   ├── generate_synthetic_data.py
│   └── creditcard.csv       # the real Kaggle file (downloaded, gitignored)
├── models/
│   ├── train_model.py       # data assessment + training + PR-AUC evaluation
│   ├── isolation_forest.joblib
│   └── scaler.joblib
├── api/
│   └── main.py               # FastAPI /score endpoint
├── streaming/
│   ├── producer.py           # replays CSV as a live Kafka feed
│   └── consumer.py           # scores via the API: retries, DLQ, idempotent writes
├── dashboard/
│   ├── app.py                 # Streamlit live view
│   └── transactions.db        # SQLite result store (created at runtime)
└── vigil-desktop/             # Tauri desktop wrapper (separate repo)
    ├── src/                    # React frontend
    ├── src-tauri/              # Rust shell + Tauri config
    ├── scripts/build.sh        # Build automation
    └── binaries/               # PyInstaller-frozen sidecar
```