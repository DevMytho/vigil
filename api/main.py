"""
Stage 2: Serving layer.

Thin HTTP wrapper around the trained model. This service knows NOTHING
about Kafka or streaming -- it just scores whatever transaction JSON it's
given. That separation is deliberate: the same endpoint could be called by
a web frontend, a batch job, or (as we'll do next) a Kafka consumer.

Run:
    uvicorn api.main:app --reload --port 8000

Test:
    curl -X POST http://localhost:8000/score -H "Content-Type: application/json" \
      -d '{"Time": 1000, "Amount": 500, "V1": 2.1, "V2": 1.8, ... }'

Desktop (Stage 1 of the vigil-desktop roadmap):
    This file is also frozen with PyInstaller into a Tauri sidecar binary
    ("vigil-api-<target-triple>"). Two things that matter for the frozen
    build:
      * model paths resolve via _model_paths(), which handles the frozen
        layout (sys._MEIPASS) as well as running from the repo;
      * the uvicorn server is started from __main__, so the frozen binary
        boots the server itself with no Python interpreter required.
"""

import csv
import io
import os
import sys
from pathlib import Path

import joblib
import numpy as np
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict


def _model_paths() -> tuple[Path, Path]:
    """Resolve model artifact paths for both dev and frozen (PyInstaller) runs.

    Dev:        uvicorn api.main:app  -> repo root is CWD, models/ is there.
    Frozen:     PyInstaller onefile extracts data files under sys._MEIPASS
                (a temp dir) -- bundle models/ next to api/main.py via
                --add-data and read them from there.
    """
    base = Path(getattr(sys, "_MEIPASS", Path.cwd()))
    return base / "models" / "isolation_forest.joblib", base / "models" / "scaler.joblib"


# Allow overriding without rebuilding when running in dev (frozen builds bake
# these in via the defaults).
MODEL_PATH = Path(os.environ.get("VIGIL_MODEL", "")) if os.environ.get("VIGIL_MODEL") else _model_paths()[0]
SCALER_PATH = Path(os.environ.get("VIGIL_SCALER", "")) if os.environ.get("VIGIL_SCALER") else _model_paths()[1]

app = FastAPI(title="VIGIL — Fraud Detection API")

# The desktop UI (Tauri webview, tauri://localhost in dev/prod) talks to this
# API over http://localhost:8000 -- cross-origin from the webview's origin,
# so CORS must be permissive here. The API only ever listens on localhost,
# so this exposes nothing to the outside world.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["tauri://localhost", "http://tauri.localhost", "http://localhost:5173", "http://localhost:1420"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load once at startup -- NOT per-request. This is the whole reason
# training and serving are decoupled: loading a model from disk is
# expensive; we pay that cost once, not on every HTTP call.
_model = joblib.load(MODEL_PATH)
_bundle = joblib.load(SCALER_PATH)
_scaler = _bundle["scaler"]
_model_features = _bundle["model_features"]  # e.g. V1..V28, Amount (no Time)


class Transaction(BaseModel):
    model_config = ConfigDict(extra="allow")  # allow V1..V28 as dynamic fields
    Time: float
    Amount: float


class ScoreResponse(BaseModel):
    is_fraud: bool
    anomaly_score: float
    threshold_used: str


class BatchSummary(BaseModel):
    total_scored: int
    flagged: int
    # Flagged rows echoed back with their original CSV row number (header=1)
    # so the UI can show/highlight/export them. All values as strings to
    # round-trip the CSV exactly as parsed.
    flagged_rows: list[dict[str, str]]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/score", response_model=ScoreResponse)
def score(txn: Transaction):
    raw = txn.model_dump()
    row = dict(raw)
    missing = [f for f in _model_features if f not in row]
    if missing:
        # Name the missing features instead of a bare KeyError -> 500.
        raise HTTPException(
            status_code=422,
            detail=f"missing feature(s): {', '.join(missing)} -- the model needs all of {', '.join(_model_features)}",
        )
    row["Amount"] = _scaler.transform([[raw["Amount"]]])[0][0]

    # Build the feature vector in the exact order the model was trained on.
    x = np.array([[row[f] for f in _model_features]])

    # decision_function: higher = more normal, lower = more anomalous.
    raw_score = _model.decision_function(x)[0]
    prediction = _model.predict(x)[0]  # 1 = normal, -1 = anomaly

    return ScoreResponse(
        is_fraud=bool(prediction == -1),
        anomaly_score=float(raw_score),
        threshold_used="IsolationForest default contamination threshold",
    )


def _score_matrix(rows: list[dict[str, str]]) -> np.ndarray:
    """Parse CSV rows into the exact feature matrix the model was trained on.

    Vectorized on purpose: one scaler.transform + one model.predict over the
    whole file beats ~280k individual per-row sklearn calls by orders of
    magnitude. Same math as /score, just batched.
    """
    amounts = np.array([[float(r["Amount"])] for r in rows])
    scaled_amounts = _scaler.transform(amounts)[:, 0]

    x = np.empty((len(rows), len(_model_features)))
    for j, f in enumerate(_model_features):
        if f == "Amount":
            x[:, j] = scaled_amounts
        else:
            x[:, j] = [float(r[f]) for r in rows]
    return x


@app.post("/score_batch", response_model=BatchSummary)
async def score_batch(file: UploadFile = File(...)):
    """Score a full CSV of transactions (e.g. data/creditcard.csv).

    Expects the dataset schema: a header row with at least Time, Amount and
    the V1..V28 feature columns. Rows the model flags as anomalies come back
    with their 1-based CSV row number (header = row 1).
    """
    text = (await file.read()).decode("utf-8-sig")  # tolerate Excel BOM
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames
    missing = [f for f in ["Time", "Amount", *_model_features] if f not in (fieldnames or [])]
    if missing:
        # Wrong CSV schema -> 422, so the UI can show a real error message.
        raise HTTPException(status_code=422, detail=f"missing column(s): {', '.join(missing)}")

    rows: list[tuple[int, dict[str, str]]] = []  # (csv line number, row)
    for i, row in enumerate(reader, start=2):  # header is line 1
        if not any((v or "").strip() for v in row.values()):
            continue  # skip blank lines
        try:
            # Validate parseability now so the error can name the row.
            [float(row[f]) for f in _model_features]
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"row {i}: could not parse numeric features")
        rows.append((i, row))

    if not rows:
        return BatchSummary(total_scored=0, flagged=0, flagged_rows=[])

    predictions = _model.predict(_score_matrix([r for _, r in rows]))  # 1 = normal, -1 = anomaly
    flagged_rows = [
        {"_row": str(i), **{k: row[k] for k in fieldnames}}
        for (i, row), pred in zip(rows, predictions)
        if pred == -1
    ]

    return BatchSummary(total_scored=len(rows), flagged=len(flagged_rows), flagged_rows=flagged_rows)


if __name__ == "__main__":
    # Frozen-entrypoint path: the sidecar binary boots uvicorn itself, bound
    # to localhost only. Dev runs go through `uvicorn api.main:app` above.
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
