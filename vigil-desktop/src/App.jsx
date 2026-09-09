import { useState, useRef, useEffect } from "react";
import { listen } from "@tauri-apps/api/event";

const API_BASE = "http://localhost:8000";
const VERSION = "0.1.0";

const STATUS_COLORS = {
  starting: "var(--text-secondary)",
  connecting: "#f39c12",
  connected: "var(--success)",
  error: "var(--danger)",
};

export default function App() {
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [showAbout, setShowAbout] = useState(false);
  const [sidecar, setSidecar] = useState({ status: "starting", message: "Starting API…" });
  const fileInputRef = useRef(null);

  // Listen for sidecar health check events from Rust
  useEffect(() => {
    const unlisten = listen("sidecar-status", (event) => {
      setSidecar(event.payload);
    });
    return () => { unlisten.then((fn) => fn()); };
  }, []);

  async function handleFile(file) {
    if (!file) return;
    setLoading(true);
    setError(null);
    setResults(null);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch(`${API_BASE}/score_batch`, {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Server returned ${res.status}`);
      }

      const data = await res.json();
      setResults(data);
    } catch (err) {
      setError(err.message || "Failed to score CSV");
    } finally {
      setLoading(false);
    }
  }

  function handleDrop(e) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file?.name.endsWith(".csv")) handleFile(file);
  }

  function handleDragOver(e) {
    e.preventDefault();
    setDragging(true);
  }

  function handleDragLeave() {
    setDragging(false);
  }

  function handleInputChange(e) {
    handleFile(e.target.files[0]);
  }

  function handleExport() {
    if (!results?.flagged_rows?.length) return;
    const rows = results.flagged_rows;
    const headers = Object.keys(rows[0]).filter((h) => h !== "_row");
    const csv = [
      ["_row", ...headers].join(","),
      ...rows.map((r) => ["_row", ...headers].map((h) => r[h] ?? "").join(",")),
    ].join("\n");

    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "flagged_transactions.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>
            <span>VIGIL</span> Fraud Detection
          </h1>
          <div className="subtitle">Isolation-based guard for illicit ledger activity</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {loading && (
            <div className="spinner" style={{ width: 16, height: 16, borderWidth: 2 }} />
          )}

          {/* Sidecar health indicator */}
          <div
            className="health-badge"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "4px 10px",
              borderRadius: "var(--radius)",
              background: "var(--bg-tertiary)",
              border: "1px solid var(--border)",
              fontSize: "0.72rem",
              color: STATUS_COLORS[sidecar.status] || "var(--text-secondary)",
              transition: "color 0.3s",
            }}
            title={sidecar.message}
          >
            {sidecar.status === "connected" ? (
              <span style={{ fontSize: 10 }}>●</span>
            ) : sidecar.status === "error" ? (
              <span style={{ fontSize: 10 }}>●</span>
            ) : (
              <div className="spinner" style={{ width: 10, height: 10, borderWidth: 1.5 }} />
            )}
            <span style={{ fontFamily: "var(--font-mono)" }}>
              {sidecar.status === "connected" ? "API Ready" :
               sidecar.status === "error" ? "Offline" :
               sidecar.status === "starting" ? "Starting…" : "Connecting…"}
            </span>
          </div>

          <button
            className="btn btn-secondary"
            style={{ padding: "4px 10px", fontSize: "0.75rem" }}
            onClick={() => setShowAbout(!showAbout)}
          >
            About
          </button>
        </div>
      </header>

      <main className="content">
        {error && (
          <div className="error-banner">
            <strong>Error:</strong> {error}
          </div>
        )}

        {loading ? (
          <div className="loading-overlay">
            <div className="spinner" />
            <div className="loading-text">Scoring transactions…</div>
          </div>
        ) : !results ? (
          <div
            className={`upload-zone ${dragging ? "dragging" : ""}`}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onClick={() => fileInputRef.current?.click()}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv"
              onChange={handleInputChange}
              style={{ display: "none" }}
            />
            <div className="icon">📄</div>
            <h2>Load CSV</h2>
            <p>Drop a transaction CSV here, or click to browse</p>
            <p style={{ marginTop: 8, fontSize: "0.75rem" }}>
              Expects columns: Time, Amount, V1–V28
            </p>
          </div>
        ) : (
          <>
            <div className="stats-bar">
              <div className="stat-card">
                <div className="label">Total Scored</div>
                <div className="value">{results.total_scored.toLocaleString()}</div>
              </div>
              <div className="stat-card">
                <div className="label">Flagged</div>
                <div className="value danger">{results.flagged.toLocaleString()}</div>
              </div>
              <div className="stat-card">
                <div className="label">Clean</div>
                <div className="value success">
                  {(results.total_scored - results.flagged).toLocaleString()}
                </div>
              </div>
            </div>

            <div className="results-header">
              <h2>Flagged Transactions ({results.flagged})</h2>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn btn-primary" onClick={handleExport}>
                  ⬇ Export CSV
                </button>
                <button
                  className="btn btn-secondary"
                  onClick={() => { setResults(null); setError(null); }}
                >
                  ← Load Another
                </button>
              </div>
            </div>

            {results.flagged_rows.length > 0 ? (
              <div className="table-wrapper" style={{ maxHeight: "60vh", overflowY: "auto" }}>
                <table>
                  <thead>
                    <tr>
                      <th>Row</th>
                      {Object.keys(results.flagged_rows[0])
                        .filter((h) => h !== "_row")
                        .map((h) => (
                          <th key={h}>{h}</th>
                        ))}
                    </tr>
                  </thead>
                  <tbody>
                    {results.flagged_rows.map((row, i) => (
                      <tr key={i} className="flagged">
                        <td>{row._row}</td>
                        {Object.keys(row)
                          .filter((h) => h !== "_row")
                          .map((h) => (
                            <td key={h}>{row[h]}</td>
                          ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty-state">
                <div className="icon">✅</div>
                <p>No anomalies detected in this dataset.</p>
              </div>
            )}
          </>
        )}
      </main>

      {showAbout && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.6)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
          onClick={() => setShowAbout(false)}
        >
          <div
            style={{
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              padding: "32px 40px",
              maxWidth: 400,
              textAlign: "center",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ fontSize: "2.5rem", marginBottom: 12 }}>🛡️</div>
            <h2 style={{ fontSize: "1.2rem", marginBottom: 8 }}>
              <span style={{ color: "var(--accent)" }}>VIGIL</span> Desktop
            </h2>
            <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem", marginBottom: 4 }}>
              v{VERSION}
            </p>
            <p style={{ color: "var(--text-secondary)", fontSize: "0.8rem", marginBottom: 16 }}>
              Isolation-based guard for illicit ledger activity.
              <br />Anomaly detection powered by Isolation Forest.
            </p>
            <p style={{ color: "var(--text-secondary)", fontSize: "0.75rem" }}>
              Built with Tauri + React + FastAPI + scikit-learn
            </p>
            <button
              className="btn btn-primary"
              style={{ marginTop: 16 }}
              onClick={() => setShowAbout(false)}
            >
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
