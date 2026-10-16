#!/usr/bin/env bash
# install.sh — Automated installer for caelestia-material-cursor

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ICONS_DIR="$HOME/.icons"
DATA_ICONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/caelestia"
CLI_CONF="$CONFIG_DIR/cli.json"
PLUGINS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/caelestia/plugins"
PLUGIN_TARGET="$PLUGINS_DIR/caelestia-material-cursor"

echo "=========================================================="
echo "    Caelestia Material Cursor Sync Installer"
echo "=========================================================="

# 0. Check system dependencies
echo "-> Checking dependencies..."

if ! command -v python3 &>/dev/null; then
    echo "❌ Error: python3 is not installed. Please install Python 3." >&2
    exit 1
fi

if ! python3 -c "import PIL" 2>/dev/null; then
    echo "⚠️  Pillow (PIL) is not installed in your Python environment."
    echo "   Attempting to install via pip: pip install -r '$SCRIPT_DIR/requirements.txt'..."
    if command -v pip3 &>/dev/null || command -v pip &>/dev/null; then
        pip install -r "$SCRIPT_DIR/requirements.txt" || pip3 install -r "$SCRIPT_DIR/requirements.txt" || {
            echo "❌ Failed to install Pillow via pip."
            echo "   Please install it using your system package manager:"
            echo "     Arch/CachyOS: sudo pacman -S python-pillow"
            echo "     Debian/Ubuntu: sudo apt install python3-pil"
            echo "     Fedora:        sudo dnf install python3-pillow"
            exit 1
        }
    else
        echo "❌ pip is not available. Please install python-pillow using your package manager:"
        echo "     Arch/CachyOS: sudo pacman -S python-pillow"
        echo "     Debian/Ubuntu: sudo apt install python3-pil"
        echo "     Fedora:        sudo dnf install python3-pillow"
        exit 1
    fi
fi

DOWNLOADER=""
if command -v curl &>/dev/null; then
    DOWNLOADER="curl"
elif command -v wget &>/dev/null; then
    DOWNLOADER="wget"
else
    echo "❌ Error: Neither 'curl' nor 'wget' was found. Please install curl or wget." >&2
    exit 1
fi

if ! command -v tar &>/dev/null; then
    echo "❌ Error: 'tar' is required to extract theme packages." >&2
    exit 1
fi

echo "   ✓ Dependencies satisfied (Python 3, Pillow, $DOWNLOADER, tar)."

chmod +x "$SCRIPT_DIR/cursor_sync.py" "$SCRIPT_DIR/color_match.py"

mkdir -p "$ICONS_DIR"
mkdir -p "$DATA_ICONS_DIR"
mkdir -p "$CONFIG_DIR"
mkdir -p "$PLUGINS_DIR"
mkdir -p "$SCRIPT_DIR/assets"

# 1. Detect monitor resolution and determine optimal cursor size
echo "-> Detecting monitor resolution & optimal cursor size..."
DETECT_JSON=$(python3 "$SCRIPT_DIR/cursor_sync.py" --detect-size 2>/dev/null || echo "{}")

REC_SIZE=$(echo "$DETECT_JSON" | grep -o '"recommended_size": [0-9]*' | awk '{print $2}')
[ -z "$REC_SIZE" ] && REC_SIZE=24

MON_NAME=$(echo "$DETECT_JSON" | grep -o '"name": "[^"]*"' | cut -d'"' -f4)
MON_WIDTH=$(echo "$DETECT_JSON" | grep -o '"width": [0-9]*' | awk '{print $2}')
MON_HEIGHT=$(echo "$DETECT_JSON" | grep -o '"height": [0-9]*' | awk '{print $2}')
MON_SCALE=$(echo "$DETECT_JSON" | grep -o '"scale": [0-9.]*' | awk '{print $2}')

echo "   * Primary monitor: ${MON_NAME:-default} (${MON_WIDTH:-1920}x${MON_HEIGHT:-1080} @ ${MON_SCALE:-1.0}x)"
echo "   * Suggested cursor size: ${REC_SIZE}px"

CHOSEN_SIZE="$REC_SIZE"
if [ -t 0 ] && [ "$1" != "-y" ] && [ "$1" != "--yes" ]; then
    echo ""
    echo "   Suggested Cursor Sizes for your resolution:"
    echo "     1) 24px (Standard, crisp on 1080p or scaled HiDPI)"
    echo "     2) 28px (Slightly larger, high visibility)"
    echo "     3) 32px (Large, high accessibility)"
    echo "     4) 48px (Extra large / unscaled 4K)"
    echo ""
    read -r -p "   Select size [1-4 or enter custom size, default: ${REC_SIZE}px]: " USER_IN
    case "$USER_IN" in
        1) CHOSEN_SIZE=24 ;;
        2) CHOSEN_SIZE=28 ;;
        3) CHOSEN_SIZE=32 ;;
        4) CHOSEN_SIZE=48 ;;
        "") CHOSEN_SIZE="$REC_SIZE" ;;
        *)
            if [[ "$USER_IN" =~ ^[0-9]+$ ]] && [ "$USER_IN" -ge 16 ] && [ "$USER_IN" -le 96 ]; then
                CHOSEN_SIZE="$USER_IN"
            else
                echo "   ! Invalid selection, using recommended ${REC_SIZE}px."
                CHOSEN_SIZE="$REC_SIZE"
            fi
            ;;
    esac
fi

echo "   ✓ Selected cursor size: ${CHOSEN_SIZE}px"

# 2. Check and unpack themes
echo "-> Checking cursor theme packages..."

FOUND_THEMES=$(find "$ICONS_DIR" -maxdepth 1 -name "Bibata-Material-*" 2>/dev/null | wc -l)

if [ "$FOUND_THEMES" -lt 10 ]; then
    echo "-> Setting up Bibata-Material theme packages (v1.3.0)..."
    TMP_DIR=$(mktemp -d)
    DARK_URL="https://github.com/SakibShahariar/material-bibata-cursor/releases/download/v1.3.0/bibata-material-dark-v1.3.0.tar.gz"
    LIGHT_URL="https://github.com/SakibShahariar/material-bibata-cursor/releases/download/v1.3.0/bibata-material-light-v1.3.0.tar.gz"

    LOCAL_DARK=$(find "$SCRIPT_DIR" -maxdepth 1 -name "*bibata-material-dark*.tar.gz" 2>/dev/null | head -n 1)
    LOCAL_LIGHT=$(find "$SCRIPT_DIR" -maxdepth 1 -name "*bibata-material-light*.tar.gz" 2>/dev/null | head -n 1)

    if [ -n "$LOCAL_DARK" ] && [ -f "$LOCAL_DARK" ]; then
        echo "   * Using local dark theme archive: $(basename "$LOCAL_DARK")"
        cp "$LOCAL_DARK" "$TMP_DIR/dark.tar.gz"
    else
        echo "   * Downloading Bibata-Material Dark theme pack (v1.3.0)..."
        if [ "$DOWNLOADER" = "curl" ]; then
            curl -fL --progress-bar -o "$TMP_DIR/dark.tar.gz" "$DARK_URL" || true
        else
            wget -q --show-progress -O "$TMP_DIR/dark.tar.gz" "$DARK_URL" || true
        fi
    fi

    if [ -f "$TMP_DIR/dark.tar.gz" ] && [ -s "$TMP_DIR/dark.tar.gz" ]; then
        tar -xzf "$TMP_DIR/dark.tar.gz" -C "$TMP_DIR"
        cp -rn "$TMP_DIR"/bibata-material-dark-*/Bibata-Material-* "$ICONS_DIR/" 2>/dev/null || true
        cp -rn "$TMP_DIR"/bibata-material-dark-*/Bibata-Material-* "$DATA_ICONS_DIR/" 2>/dev/null || true
    else
        echo "⚠️  Failed to acquire dark theme pack. Check internet connection or download manually from:"
        echo "   $DARK_URL"
    fi

    if [ -n "$LOCAL_LIGHT" ] && [ -f "$LOCAL_LIGHT" ]; then
        echo "   * Using local light theme archive: $(basename "$LOCAL_LIGHT")"
        cp "$LOCAL_LIGHT" "$TMP_DIR/light.tar.gz"
    else
        echo "   * Downloading Bibata-Material Light theme pack (v1.3.0)..."
        if [ "$DOWNLOADER" = "curl" ]; then
            curl -fL --progress-bar -o "$TMP_DIR/light.tar.gz" "$LIGHT_URL" || true
        else
            wget -q --show-progress -O "$TMP_DIR/light.tar.gz" "$LIGHT_URL" || true
        fi
    fi

    if [ -f "$TMP_DIR/light.tar.gz" ] && [ -s "$TMP_DIR/light.tar.gz" ]; then
        tar -xzf "$TMP_DIR/light.tar.gz" -C "$TMP_DIR"
        cp -rn "$TMP_DIR"/bibata-material-light-*/Bibata-Material-* "$ICONS_DIR/" 2>/dev/null || true
        cp -rn "$TMP_DIR"/bibata-material-light-*/Bibata-Material-* "$DATA_ICONS_DIR/" 2>/dev/null || true
    else
        echo "⚠️  Failed to acquire light theme pack. Check internet connection or download manually from:"
        echo "   $LIGHT_URL"
    fi

    rm -rf "$TMP_DIR"
fi

FOUND_NOW=$(find "$ICONS_DIR" -maxdepth 1 -name "Bibata-Material-*" 2>/dev/null | wc -l)
if [ "$FOUND_NOW" -lt 5 ]; then
    echo "❌ Error: Could not locate installed Bibata-Material themes in $ICONS_DIR."
    echo "   Please download the release archives from:"
    echo "   https://github.com/SakibShahariar/material-bibata-cursor/releases"
    echo "   and extract them into ~/.icons/ before running install.sh again."
    exit 1
fi
echo "   ✓ Themes ready: $FOUND_NOW themes installed in $ICONS_DIR"

# 3. Optimize theme storage (strip unused sizes and delete temporary archive directories)
echo "-> Optimizing theme storage to ${CHOSEN_SIZE}px..."
python3 "$SCRIPT_DIR/cursor_sync.py" --optimize "$CHOSEN_SIZE"

# Save selected cursor size to plugin configuration and ensure Hyprland environment sync
python3 -c "import sys; sys.path.insert(0, '$SCRIPT_DIR'); import cursor_sync; cfg = cursor_sync.load_plugin_config(); cfg['size'] = $CHOSEN_SIZE; cursor_sync.save_plugin_config(cfg); cursor_sync.ensure_hypr_env_lua()"

# 4. Extract fixed notification icon (Bibata-Material-Noir-Light)
if [ ! -f "$SCRIPT_DIR/assets/cursor_icon.png" ]; then
    echo "-> Generating fixed notification icon (Bibata-Material-Noir-Light)..."
    NOIR_HLC="$ICONS_DIR/Bibata-Material-Noir-Light/hyprcursors/left_ptr.hlc"
    if [ -f "$NOIR_HLC" ]; then
        unzip -p "$NOIR_HLC" left_ptr.svg > "$SCRIPT_DIR/assets/cursor_icon.svg" 2>/dev/null || true
        if command -v rsvg-convert &>/dev/null; then
            rsvg-convert -w 128 -h 128 "$SCRIPT_DIR/assets/cursor_icon.svg" -o "$SCRIPT_DIR/assets/cursor_icon.png" 2>/dev/null || true
        elif command -v magick &>/dev/null; then
            magick "$SCRIPT_DIR/assets/cursor_icon.svg" -resize 128x128 "$SCRIPT_DIR/assets/cursor_icon.png" 2>/dev/null || true
        fi
    fi
fi
echo "   ✓ Notification icon configured."

# 5. Configure postHooks in ~/.config/caelestia/cli.json
echo "-> Configuring postHooks in Caelestia CLI ($CLI_CONF)..."
SYNC_CMD="python3 $SCRIPT_DIR/cursor_sync.py"

python3 - <<EOF
import json
import os

cli_path = "$CLI_CONF"
sync_cmd = "$SYNC_CMD"

cfg = {}
if os.path.exists(cli_path):
    try:
        with open(cli_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

for section in ["theme", "wallpaper"]:
    if section not in cfg:
        cfg[section] = {}
    curr_hook = cfg[section].get("postHook", "")
    if sync_cmd not in curr_hook:
        if curr_hook:
            cfg[section]["postHook"] = f"{curr_hook}; {sync_cmd}"
        else:
            cfg[section]["postHook"] = sync_cmd

with open(cli_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=4)

print("   ✓ Hooks registered in theme.postHook and wallpaper.postHook.")
EOF

# 6. Link plugin in Caelestia plugins directory
echo "-> Linking plugin to $PLUGINS_DIR..."
rm -rf "$PLUGIN_TARGET"
ln -sf "$SCRIPT_DIR" "$PLUGIN_TARGET"
echo "   ✓ Link created: $PLUGIN_TARGET -> $SCRIPT_DIR"

# 7. Index wallpapers database and run initial sync
echo "-> Pre-indexing wallpaper database..."
python3 "$SCRIPT_DIR/cursor_sync.py" --scan

echo "-> Synchronizing initial cursor..."
python3 "$SCRIPT_DIR/cursor_sync.py"

echo "=========================================================="
echo "    Installation Complete! Storage optimized & cursor synced."
echo "=========================================================="
