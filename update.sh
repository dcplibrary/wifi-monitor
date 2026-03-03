#!/bin/bash
# WiFi Monitor Update Script
# Updates the service from GitHub without requiring Git
# Run with sudo if installed as systemd service

set -e

REPO="${REPO:-dcplibrary/wifi-monitor}"
BRANCH="${BRANCH:-main}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "=== WiFi Monitor Update Script ==="
echo "Repository: $REPO"
echo "Branch: $BRANCH"
echo ""

# Check if systemd service exists
SERVICE_NAME="wifi-monitor"
if systemctl list-unit-files | grep -q "^${SERVICE_NAME}.service"; then
    HAS_SERVICE=true
    echo "[1/5] Stopping systemd service..."
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    echo "      Service stopped"
else
    HAS_SERVICE=false
    echo "[1/5] No systemd service found (standalone mode)"
fi

# Download latest code
echo "[2/5] Downloading latest version from GitHub..."
ZIP_URL="https://github.com/${REPO}/archive/refs/heads/${BRANCH}.zip"
TMP_DIR=$(mktemp -d)
ZIP_FILE="$TMP_DIR/wifi-monitor.zip"

if command -v curl >/dev/null 2>&1; then
    curl -L -o "$ZIP_FILE" "$ZIP_URL"
elif command -v wget >/dev/null 2>&1; then
    wget -O "$ZIP_FILE" "$ZIP_URL"
else
    echo "ERROR: curl or wget required"
    exit 1
fi
echo "      Downloaded successfully"

# Extract
echo "[3/5] Extracting files..."
unzip -q "$ZIP_FILE" -d "$TMP_DIR"
SOURCE_DIR=$(find "$TMP_DIR" -maxdepth 1 -type d -name "wifi-monitor-*" | head -n 1)
echo "      Extracted to temp location"

# Copy files (preserve .env, database, and logs)
echo "[4/5] Updating files (preserving config and data)..."
cd "$SOURCE_DIR"
find . -type f ! -name ".env" ! -name "wireless_stats.db" ! -name "wireless_service.log" ! -path "*/.git/*" -exec sh -c '
    for file; do
        mkdir -p "'"$SCRIPT_DIR"'/$(dirname "$file")"
        cp -f "$file" "'"$SCRIPT_DIR"'/$file"
    done
' sh {} +
echo "      Files updated"

# Cleanup
echo "[5/5] Cleaning up..."
rm -rf "$TMP_DIR"
echo "      Cleanup complete"

# Restart service
if [ "$HAS_SERVICE" = true ]; then
    echo ""
    echo "Restarting systemd service..."
    sudo systemctl daemon-reload
    sudo systemctl start "$SERVICE_NAME"
    sleep 2
    
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "Service restarted successfully"
    else
        echo "WARNING: Service did not start"
        echo "Check logs: sudo journalctl -u $SERVICE_NAME -n 50"
    fi
fi

echo ""
echo "=== Update Complete ==="
echo "Current directory: $SCRIPT_DIR"
echo ""
