#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export REPO_DIR
OPT_DIR="$HOME/.local/opt/agy-otel"
CONFIG_DIR="$HOME/.config/agy-otel"
GEMINI_CONFIG="$HOME/.gemini/config"

echo "==> Setting up Python virtual environment..."
if [ ! -d "$OPT_DIR" ]; then
  python3 -m venv "$OPT_DIR"
fi

echo "==> Installing OpenTelemetry dependencies..."
"$OPT_DIR/bin/pip" install --quiet opentelemetry-sdk opentelemetry-exporter-otlp-proto-http

echo "==> Copying hook.py..."
mkdir -p "$OPT_DIR"
cp "$REPO_DIR/.local/opt/agy-otel/hook.py" "$OPT_DIR/hook.py"
chmod +x "$OPT_DIR/hook.py"

echo "==> Setting up configuration..."
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/env" ]; then
  cp "$REPO_DIR/.config/agy-otel/env" "$CONFIG_DIR/env"
else
  echo "    $CONFIG_DIR/env already exists, leaving untouched."
fi
chmod 600 "$CONFIG_DIR/env"

echo "==> Registering hook in $GEMINI_CONFIG/hooks.json..."
mkdir -p "$GEMINI_CONFIG"
python3 - <<'EOF'
import json, os

hooks_path = os.path.expanduser("~/.gemini/config/hooks.json")
repo_dir = os.environ.get("REPO_DIR", ".")
repo_hooks_path = os.path.join(repo_dir, ".gemini/config/hooks.json")

with open(repo_hooks_path) as f:
    repo_hooks = json.load(f)

existing = {}
if os.path.exists(hooks_path):
    try:
        with open(hooks_path) as f:
            existing = json.load(f)
    except Exception:
        existing = {}

# Merge agy-otel without overwriting other hooks (e.g. orca-status)
existing["agy-otel"] = repo_hooks["agy-otel"]

with open(hooks_path, "w") as f:
    json.dump(existing, f, indent=2)
EOF

echo "==> Done! agy-otel is installed."
