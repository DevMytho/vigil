use std::time::Duration;
use tauri::Emitter;
use tauri_plugin_shell::ShellExt;

/// Polls `http://localhost:8000/health` until it responds or we time out.
/// Emits `sidecar-status` events so the React frontend can show connection state.
fn wait_for_sidecar(app: &tauri::AppHandle) {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .expect("failed to create HTTP client");

    let max_attempts = 30; // 30 × 500ms = 15s max wait
    for attempt in 1..=max_attempts {
        match client.get("http://localhost:8000/health").send() {
            Ok(resp) if resp.status().is_success() => {
                let _ = app.emit("sidecar-status", serde_json::json!({
                    "status": "connected",
                    "message": "API ready",
                }));
                return;
            }
            _ => {
                let _ = app.emit("sidecar-status", serde_json::json!({
                    "status": "connecting",
                    "message": format!("Waiting for API… ({}/{})", attempt, max_attempts),
                }));
                std::thread::sleep(Duration::from_millis(500));
            }
        }
    }

    let _ = app.emit("sidecar-status", serde_json::json!({
        "status": "error",
        "message": "Sidecar failed to start — health check timed out",
    }));
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // Emit initial status
            let _ = app.emit("sidecar-status", serde_json::json!({
                "status": "starting",
                "message": "Starting API…",
            }));

            // Spawn the VIGIL API sidecar on app startup.
            let sidecar_command = app.shell().sidecar("vigil-api").expect(
                "failed to create sidecar command — is the binary in src-tauri/binaries/?",
            );

            let (_rx, _child) = sidecar_command
                .spawn()
                .expect("failed to spawn vigil-api sidecar");

            // Spawn a background thread to poll the health endpoint.
            let app_handle = app.handle().clone();
            std::thread::spawn(move || {
                wait_for_sidecar(&app_handle);
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
