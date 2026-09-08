"""
Stage 5: Dashboard.

Polls the SQLite table the consumer is writing to and shows a live-updating
view of scored transactions -- this is what makes the project watchable
for a demo GIF/video instead of just terminal logs.

Two separate query shapes, on purpose:
  1. GLOBAL STATS -- a single SQL aggregate query (COUNT/SUM) that scans
     just the columns it needs and returns 4 numbers, run against the
     whole table. This is cheap even at millions of rows because SQLite
     computes the aggregate itself; we never pull full rows into Python
     for this. Indexes on predicted_fraud/true_label (added in
     streaming/consumer.py's init_db) make this fast as the table grows.
  2. RECENT ACTIVITY -- a small LIMIT 50 query, just for the "what's
     happening right now" table. This is intentionally NOT the source of
     the headline totals anymore -- it's a preview, not a stat.

Run (after producer + consumer have written some data):
    streamlit run dashboard/app.py
"""

import sqlite3
import time

import pandas as pd
import streamlit as st

DB_PATH = "dashboard/transactions.db"
RECENT_LIMIT = 50

st.set_page_config(page_title="VIGIL — Fraud Detection Dashboard", layout="wide")
st.title("VIGIL")
st.caption("Vigilant Isolation-based Guard for Illicit Ledger-activity")

placeholder = st.empty()


def fetch_global_stats(conn) -> dict:
    """One aggregate query, whole table, no row-by-row loading."""
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(predicted_fraud), 0) AS flagged,
            COALESCE(SUM(true_label), 0) AS true_fraud,
            COALESCE(SUM(CASE WHEN predicted_fraud = 1 AND true_label = 1 THEN 1 ELSE 0 END), 0) AS caught,
            COALESCE(SUM(CASE WHEN predicted_fraud = 1 AND true_label = 0 THEN 1 ELSE 0 END), 0) AS false_positives
        FROM scored_transactions
        """
    ).fetchone()
    return {
        "total": row[0],
        "flagged": row[1],
        "true_fraud": row[2],
        "caught": row[3],
        "false_positives": row[4],
    }


def fetch_recent(conn, limit: int) -> pd.DataFrame:
    return pd.read_sql_query(
        f"SELECT * FROM scored_transactions ORDER BY id DESC LIMIT {limit}", conn
    )


while True:
    try:
        conn = sqlite3.connect(DB_PATH)
        stats = fetch_global_stats(conn)
        recent_df = fetch_recent(conn, RECENT_LIMIT)
        conn.close()
    except Exception:
        stats = {"total": 0, "flagged": 0, "true_fraud": 0, "caught": 0, "false_positives": 0}
        recent_df = pd.DataFrame()

    with placeholder.container():
        if stats["total"] == 0:
            st.info("Waiting for transactions... start the producer and consumer.")
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total transactions processed", stats["total"])
            c2.metric("Total flagged as fraud", stats["flagged"])
            c3.metric(
                "Fraud caught",
                f"{stats['caught']}/{stats['true_fraud']}" if stats["true_fraud"] else "0/0",
            )
            c4.metric("Total false positives", stats["false_positives"])

            if not recent_df.empty:
                st.subheader(f"Recently flagged (last {RECENT_LIMIT} transactions seen)")
                flagged_df = recent_df[recent_df["predicted_fraud"] == 1].copy()
                if not flagged_df.empty:
                    flagged_df["status"] = flagged_df.apply(
                        lambda r: "✅ true fraud" if r["true_label"] == 1 else "⚠️ false positive",
                        axis=1,
                    )
                    st.dataframe(
                        flagged_df[["received_at", "amount", "anomaly_score", "status"]],
                        use_container_width=True,
                    )
                else:
                    st.caption("No fraud flagged in the most recent window.")

                st.subheader(f"Recent activity (last {RECENT_LIMIT} transactions)")
                st.dataframe(recent_df, use_container_width=True)

    time.sleep(2)
