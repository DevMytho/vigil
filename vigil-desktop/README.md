# VIGIL Desktop

Tauri desktop wrapper around the VIGIL fraud-detection API. The Python
FastAPI service gets frozen into a standalone binary via PyInstaller and
run as a Tauri **sidecar** process. The React frontend talks to it over
`localhost` like any web app.

```
vigil-desktop/
├── src/                    # React frontend (Vite + React 18)
├── src-tauri/              # Rust shell + Tauri config
│   ├── src/lib.rs          # Sidecar spawn + health polling
│   ├── binaries/           # PyInstaller-frozen API binary
│   ├── capabilities/       # Tauri permissions
│   └── tauri.conf.json     # App config, sidecar declaration
├── scripts/build.sh        # Build automation (PyInstaller → Tauri)
├── binaries/               # Sidecar binaries (also copied to src-tauri/)
└── .github/workflows/      # CI/CD for cross-platform releases
```

## Prerequisites

- **Rust** + Cargo (`rustup`)
- **Node.js** 18+ and npm
- **Python** 3.10+ with `pyinstaller` installed (`pip install pyinstaller`)
- **Linux only**: `libwebkit2gtk-4.1-dev`, `libgtk-3-dev`, `libayatana-appindicator3-dev`

## Quick start

```bash
cd vigil-desktop
npm install
npm run tauri dev
```

This starts the Vite dev server, compiles the Rust shell, opens the
Tauri window, and spawns the sidecar API binary in the background. The
React frontend connects to `http://localhost:8000`.

## How it works

### Sidecar lifecycle

1. **Tauri starts** → spawns `vigil-api` binary via the shell plugin
2. **Rust backend** polls `localhost:8000/health` every 500ms
3. **Events emitted** to the React frontend:
   - `starting` → `connecting` → `connected` (success)
   - `starting` → `connecting` → `error` (timed out)
4. **Frontend** shows a live health badge in the header:
   - 🟡 "Connecting…" while polling
   - 🟢 "API Ready" when connected
   - 🔴 "Offline" on failure
5. **App closes** → sidecar process exits with the parent

### Frontend features

- **CSV upload**: drag-and-drop or file picker (native dialog)
- **Batch scoring**: POSTs to `/score_batch`, renders flagged rows
- **Export**: download flagged transactions as CSV
- **Dark theme**: purple accent, monospace tables, custom scrollbar
- **About modal**: version info, tech stack

## Building

### Development

```bash
npm run tauri dev          # live reload, debug build
```

### Release build

```bash
# Full build: PyInstaller freeze → sidecar copy → Tauri release build
./scripts/build.sh

# Skip PyInstaller (reuse existing binary)
./scripts/build.sh --skip-py

# Debug build (faster, no LTO)
./scripts/build.sh --debug
```

### Manual steps

If you prefer to run each step yourself:

```bash
# 1. Freeze the Python API (from the vigil/ root)
cd ../
pyinstaller vigil-api.spec --noconfirm
cp dist/vigil-api vigil-desktop/src-tauri/binaries/vigil-api-x86_64-unknown-linux-gnu

# 2. Build the frontend
cd vigil-desktop
npx vite build

# 3. Build the Tauri app
npx tauri build
```

## CI/CD (GitHub Actions)

The release workflow (`.github/workflows/release.yml`) triggers on
`git tag v*` and builds for all three platforms:

| Platform | Target triple | Output |
|----------|---------------|--------|
| Linux | `x86_64-unknown-linux-gnu` | `.deb`, `.AppImage` |
| macOS | `aarch64-apple-darwin` | `.dmg`, `.app.tar.gz` |
| Windows | `x86_64-pc-windows-msvc` | `.msi`, `.exe` |

**To release:**

```bash
git tag v0.1.0
git push origin v0.1.0
```

GitHub Actions builds the PyInstaller binary on each platform runner,
bundles it into the Tauri app, and publishes the installers to a
GitHub Release.

### Required secrets

| Secret | Purpose |
|--------|---------|
| `TAURI_SIGNING_PRIVATE_KEY` | Auto-updater signing (optional, for future use) |

## Sidecar naming convention

Tauri requires sidecar binaries to follow the pattern:

```
<name>-<target-triple>[.exe]
```

For example: `vigil-api-x86_64-unknown-linux-gnu`

The target triple for your platform:
```bash
rustc --print host-tuple
```

## Tech stack

- **Desktop shell**: [Tauri v2](https://v2.tauri.app/) (Rust)
- **Frontend**: React 18 + Vite
- **API**: FastAPI + uvicorn (frozen via PyInstaller)
- **Model**: Isolation Forest (scikit-learn)
- **Icons**: Generated with Pillow (RGBA format for Tauri)
