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
`data/creditcard.csv` (same schema, no code changes needed).

### 2. Train the model

```bash
python models/train_model.py
```

Reports class imbalance, trains an Isolation Forest on normal transactions
only, evaluates precision/recall on a held-out mixed test set, and saves
the model to `models/isolation_forest.joblib`.

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

### 6. Watch the dashboard

```bash
streamlit run dashboard/app.py
```

## Results (on synthetic data)

Isolation Forest trained on normal-only transactions, evaluated on a
held-out mix including all fraud:

- Recall on fraud: 100% (caught all synthetic fraud cases)
- Precision on fraud: ~77% (some false positives -- normal transactions
  that looked unusual enough to flag)

This precision/recall tradeoff is the central design tension in fraud
detection: too aggressive a threshold burns analyst time on false
positives; too lenient and real fraud slips through. Worth tuning the
`contamination` parameter and comparing against a second model (e.g. an
autoencoder) to see how the tradeoff shifts.

## Project structure

```
vigil/
├── docker-compose.yml       # Kafka + Zookeeper
├── requirements.txt
├── data/
│   ├── generate_synthetic_data.py
│   └── creditcard.csv       # generated or downloaded
├── models/
│   ├── train_model.py       # data assessment + training + evaluation
│   ├── isolation_forest.joblib
│   └── scaler.joblib
├── api/
│   └── main.py               # FastAPI /score endpoint
├── streaming/
│   ├── producer.py           # replays CSV as a live Kafka feed
│   └── consumer.py           # scores each message via the API, stores results
└── dashboard/
    ├── app.py                 # Streamlit live view
    └── transactions.db        # SQLite result store (created at runtime)
```
