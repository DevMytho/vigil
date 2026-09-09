#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# VIGIL Desktop — Build Script
#
# Freezes the Python API into a sidecar binary, copies it into the
# Tauri project, and produces a platform-native installer.
#
# Usage:
#   ./scripts/build.sh              # auto-detect current platform
#   ./scripts/build.sh --debug      # skip PyInstaller + debug build
#   ./scripts/build.sh --skip-py    # skip PyInstaller, reuse existing binary
#
# Requires: Python 3.10+, pyinstaller, Node 18+, Rust/Cargo, Tauri CLI
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VIGIL_ROOT="$(cd "$PROJECT_ROOT/.." && pwd)"

# ── Flags ─────────────────────────────────────────────────────────
DEBUG=false
SKIP_PY=false
for arg in "$@"; do
  case "$arg" in
    --debug)    DEBUG=true ;;
    --skip-py)  SKIP_PY=true ;;
    --help|-h)
      echo "Usage: $0 [--debug] [--skip-py]"
      exit 0
      ;;
  esac
done

# ── Detect target triple ──────────────────────────────────────────
detect_triple() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$os" in
    Linux)  echo "${arch}-unknown-linux-gnu" ;;
    Darwin) echo "${arch}-apple-darwin" ;;
    MINGW*|MSYS*|CYGWIN*) echo "${arch}-pc-windows-msvc" ;;
    *) echo "unsupported-os-${os}-${arch}" && exit 1 ;;
  esac
}

TARGET_TRIPLE="$(detect_triple)"
SIDECAR_NAME="vigil-api-${TARGET_TRIPLE}"
SIDECAR_DEST="$PROJECT_ROOT/src-tauri/binaries/${SIDECAR_NAME}"

echo "╔══════════════════════════════════════════╗"
echo "║       VIGIL Desktop — Build Script       ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  Target:  $TARGET_TRIPLE"
echo "  Debug:   $DEBUG"
echo "  SkipPy:  $SKIP_PY"
echo ""

# ── Step 1: Freeze Python API ─────────────────────────────────────
if [ "$SKIP_PY" = false ]; then
  echo "▶ Step 1/4: Freezing Python API with PyInstaller..."

  cd "$VIGIL_ROOT"
  source venv/bin/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null || {
    echo "  ⚠  Could not activate Python venv. Trying system Python."
  }

  pyinstaller vigil-api.spec --noconfirm

  # The spec file outputs to dist/vigil-api
  SOURCE_BINARY="$VIGIL_ROOT/dist/vigil-api"

  if [ ! -f "$SOURCE_BINARY" ]; then
    echo "  ✗ PyInstaller output not found at $SOURCE_BINARY"
    exit 1
  fi

  echo "  ✓ Binary built: $(ls -lh "$SOURCE_BINARY" | awk '{print $5}')"
else
  echo "▶ Step 1/4: Skipping PyInstaller (--skip-py)"
  SOURCE_BINARY="$VIGIL_ROOT/dist/vigil-api"
fi

# ── Step 2: Copy sidecar binary ───────────────────────────────────
echo ""
echo "▶ Step 2/4: Copying sidecar binary..."

mkdir -p "$PROJECT_ROOT/src-tauri/binaries"
cp "$SOURCE_BINARY" "$SIDECAR_DEST"
chmod +x "$SIDECAR_DEST"

echo "  ✓ Copied to src-tauri/binaries/$SIDECAR_NAME"

# ── Step 3: Build frontend ────────────────────────────────────────
echo ""
echo "▶ Step 3/4: Building frontend..."

cd "$PROJECT_ROOT"
npm install --silent
npx vite build

echo "  ✓ Frontend built to dist/"

# ── Step 4: Build Tauri app ───────────────────────────────────────
echo ""
echo "▶ Step 4/4: Building Tauri application..."

if [ "$DEBUG" = true ]; then
  echo "  ℹ  Debug build (skipping LTO for speed)..."
  npx tauri build --debug
else
  npx tauri build
fi

# ── Summary ───────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║            Build Complete ✓              ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Find and display the output
OUTPUT_DIR="$PROJECT_ROOT/src-tauri/target/release/bundle"
if [ "$DEBUG" = true ]; then
  OUTPUT_DIR="$PROJECT_ROOT/src-tauri/target/debug/bundle"
fi

echo "  Output directory: $OUTPUT_DIR"
echo ""
if [ -d "$OUTPUT_DIR" ]; then
  echo "  Generated artifacts:"
  find "$OUTPUT_DIR" -type f \( -name "*.deb" -o -name "*.AppImage" -o -name "*.dmg" -o -name "*.app" -o -name "*.msi" -o -name "*.exe" \) 2>/dev/null | while read -r f; do
    echo "    → $(basename "$f") ($(ls -lh "$f" | awk '{print $5}'))"
  done
fi
echo ""
echo "  Done."
