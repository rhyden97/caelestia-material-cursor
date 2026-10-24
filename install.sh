#!/usr/bin/env bash
# install.sh — Automated installer for caelestia-material-cursor
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ICONS_DIR="$HOME/.icons"
DATA_ICONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/caelestia"
CLI_CONF="$CONFIG_DIR/cli.json"
THEME_CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/caelestia/cursor-themes"

AUTO_YES=0
RUN_OPTIMIZE=0

usage() {
    cat <<'EOF'
Usage: ./install.sh [OPTIONS]

Options:
  -y, --yes       Accept the recommended cursor size (non-interactive)
  --optimize      Strip unused cursor raster sizes after installing
                  (reversible: the release archives stay in the cache)
  -h, --help      Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        -y|--yes) AUTO_YES=1 ;;
        --optimize) RUN_OPTIMIZE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
    esac
    shift
done

echo "=========================================================="
echo "    Caelestia Material Cursor Sync Installer"
echo "=========================================================="

# 0. Dependencies -----------------------------------------------------------
echo "-> Checking dependencies..."

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is not installed. Please install Python 3." >&2
    exit 1
fi

if ! python3 -c "import PIL" 2>/dev/null; then
    echo "Warning: Pillow (PIL) is not installed in this Python environment."
    echo "         Attempting to install it for the current user..."
    if command -v pip3 >/dev/null 2>&1 || command -v pip >/dev/null 2>&1; then
        PIP_BIN="$(command -v pip3 || command -v pip)"
        if ! "$PIP_BIN" install --user -r "$SCRIPT_DIR/requirements.txt"; then
            echo "Error: could not install Pillow. Use your package manager:" >&2
            echo "  Arch/CachyOS: sudo pacman -S python-pillow" >&2
            echo "  Debian/Ubuntu: sudo apt install python3-pil" >&2
            echo "  Fedora: sudo dnf install python3-pillow" >&2
            exit 1
        fi
    else
        echo "Error: pip is not available. Install python-pillow with your package manager." >&2
        exit 1
    fi
fi

DOWNLOADER=""
if command -v curl >/dev/null 2>&1; then
    DOWNLOADER="curl"
elif command -v wget >/dev/null 2>&1; then
    DOWNLOADER="wget"
else
    echo "Error: neither curl nor wget was found. Please install one of them." >&2
    exit 1
fi

if ! command -v tar >/dev/null 2>&1; then
    echo "Error: tar is required to extract theme packages." >&2
    exit 1
fi

echo "   Dependencies satisfied (Python 3, Pillow, $DOWNLOADER, tar)."

chmod +x "$SCRIPT_DIR/cursor_sync.py" "$SCRIPT_DIR/color_match.py" "$SCRIPT_DIR/fsutil.py"
mkdir -p "$ICONS_DIR" "$DATA_ICONS_DIR" "$CONFIG_DIR" "$THEME_CACHE_DIR"

# 1. Display detection ------------------------------------------------------
echo "-> Detecting monitor resolution and optimal cursor size..."
DETECT_JSON="$(python3 "$SCRIPT_DIR/cursor_sync.py" --detect-size 2>/dev/null || echo '{}')"

read -r REC_SIZE MON_NAME MON_WIDTH MON_HEIGHT MON_SCALE < <(
    python3 - "$DETECT_JSON" <<'PY'
import json
import sys

data = json.loads(sys.argv[1] or "{}")
print(
    data.get("recommended_size", 24),
    data.get("name", "default"),
    data.get("width", 1920),
    data.get("height", 1080),
    data.get("scale", 1.0),
)
PY
)

echo "   Primary monitor: ${MON_NAME} (${MON_WIDTH}x${MON_HEIGHT} @ ${MON_SCALE}x)"
echo "   Suggested cursor size: ${REC_SIZE}px"

CHOSEN_SIZE="$REC_SIZE"
if [ "$AUTO_YES" -eq 0 ] && [ -t 0 ]; then
    echo ""
    echo "   Suggested cursor sizes for your display:"
    echo "     1) 24px (Standard, crisp on 1080p or scaled HiDPI)"
    echo "     2) 28px (Slightly larger, high visibility)"
    echo "     3) 32px (Large, high accessibility)"
    echo "     4) 48px (Extra large / unscaled 4K)"
    echo ""
    read -r -p "   Select size [1-4 or custom size, default: ${REC_SIZE}px]: " USER_IN || USER_IN=""
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
                echo "   Invalid selection, using recommended ${REC_SIZE}px."
                CHOSEN_SIZE="$REC_SIZE"
            fi
            ;;
    esac
fi

echo "   Selected cursor size: ${CHOSEN_SIZE}px"

# 2. Theme packages ---------------------------------------------------------
echo "-> Checking cursor theme packages..."

fetch_archive() {
    local url="$1"
    local destination="$2"

    if [ -s "$destination" ]; then
        echo "   Using cached archive: $(basename "$destination")"
        return 0
    fi

    if [ "$DOWNLOADER" = "curl" ]; then
        curl -fL --progress-bar -o "$destination.part" "$url" || { rm -f "$destination.part"; return 1; }
    else
        wget -q --show-progress -O "$destination.part" "$url" || { rm -f "$destination.part"; return 1; }
    fi
    mv "$destination.part" "$destination"
}

record_checksum() {
    local archive="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$archive" | awk '{print $1}' > "$archive.sha256"
    fi
}

install_archive() {
    local archive="$1"
    local temp_dir
    temp_dir="$(mktemp -d)"
    if ! tar -xzf "$archive" -C "$temp_dir"; then
        rm -rf "$temp_dir"
        return 1
    fi

    while IFS= read -r -d '' theme_dir; do
        local name
        name="$(basename "$theme_dir")"
        if [ ! -d "$ICONS_DIR/$name" ]; then
            cp -a "$theme_dir" "$ICONS_DIR/$name"
        fi
    done < <(find "$temp_dir" -maxdepth 4 -type d -name 'Bibata-Material-*' -print0)

    rm -rf "$temp_dir"
}

DARK_ARCHIVE="$THEME_CACHE_DIR/bibata-material-dark-v1.3.0.tar.gz"
LIGHT_ARCHIVE="$THEME_CACHE_DIR/bibata-material-light-v1.3.0.tar.gz"
DARK_URL="https://github.com/SakibShahariar/material-bibata-cursor/releases/download/v1.3.0/bibata-material-dark-v1.3.0.tar.gz"
LIGHT_URL="https://github.com/SakibShahariar/material-bibata-cursor/releases/download/v1.3.0/bibata-material-light-v1.3.0.tar.gz"

LOCAL_DARK="$(find "$SCRIPT_DIR" -maxdepth 1 -name '*bibata-material-dark*.tar.gz' -print -quit 2>/dev/null || true)"
LOCAL_LIGHT="$(find "$SCRIPT_DIR" -maxdepth 1 -name '*bibata-material-light*.tar.gz' -print -quit 2>/dev/null || true)"

if [ -n "$LOCAL_DARK" ]; then
    echo "   Using local dark archive: $(basename "$LOCAL_DARK")"
    cp -f "$LOCAL_DARK" "$DARK_ARCHIVE"
    record_checksum "$DARK_ARCHIVE"
elif [ ! -s "$DARK_ARCHIVE" ]; then
    echo "   Downloading Bibata-Material Dark pack (v1.3.0)..."
    fetch_archive "$DARK_URL" "$DARK_ARCHIVE" && record_checksum "$DARK_ARCHIVE" \
        || echo "   Warning: dark pack download failed."
fi

if [ -n "$LOCAL_LIGHT" ]; then
    echo "   Using local light archive: $(basename "$LOCAL_LIGHT")"
    cp -f "$LOCAL_LIGHT" "$LIGHT_ARCHIVE"
    record_checksum "$LIGHT_ARCHIVE"
elif [ ! -s "$LIGHT_ARCHIVE" ]; then
    echo "   Downloading Bibata-Material Light pack (v1.3.0)..."
    fetch_archive "$LIGHT_URL" "$LIGHT_ARCHIVE" && record_checksum "$LIGHT_ARCHIVE" \
        || echo "   Warning: light pack download failed."
fi

[ -s "$DARK_ARCHIVE" ] && install_archive "$DARK_ARCHIVE" || true
[ -s "$LIGHT_ARCHIVE" ] && install_archive "$LIGHT_ARCHIVE" || true

# Link themes into the XDG data dir instead of duplicating them.
for theme_dir in "$ICONS_DIR"/Bibata-Material-*; do
    [ -d "$theme_dir" ] || continue
    name="$(basename "$theme_dir")"
    target="$DATA_ICONS_DIR/$name"
    if [ ! -e "$target" ] && [ ! -L "$target" ]; then
        ln -s "$theme_dir" "$target" 2>/dev/null || cp -a "$theme_dir" "$target"
    fi
done

FOUND_NOW="$(find "$ICONS_DIR" -maxdepth 1 -name 'Bibata-Material-*' -type d 2>/dev/null | wc -l)"
if [ "$FOUND_NOW" -lt 5 ]; then
    echo "Error: could not locate installed Bibata-Material themes in $ICONS_DIR." >&2
    echo "Download the release archives from:" >&2
    echo "  https://github.com/SakibShahariar/material-bibata-cursor/releases" >&2
    echo "and place them in $THEME_CACHE_DIR before retrying." >&2
    exit 1
fi
echo "   Themes ready: $FOUND_NOW themes installed in $ICONS_DIR"
python3 "$SCRIPT_DIR/cursor_sync.py" --normalize >/dev/null 2>&1 || true

# 3. Optional storage optimization -----------------------------------------
if [ "$RUN_OPTIMIZE" -eq 1 ]; then
    echo "-> Optimizing theme storage (reversible via --restore)..."
    python3 "$SCRIPT_DIR/cursor_sync.py" --optimize "$CHOSEN_SIZE" \
        || echo "   Warning: optimization skipped."
else
    echo "-> Keeping all cursor raster sizes (run --optimize to trim them later)."
fi

# 4. Persist config ---------------------------------------------------------
python3 - "$SCRIPT_DIR" "$CHOSEN_SIZE" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
import cursor_sync

config = cursor_sync.load_config()
config["size"] = int(sys.argv[2])
cursor_sync.save_config(config)
cursor_sync.ensure_hypr_env_lua()
PY

# 5. Register Caelestia postHooks ------------------------------------------
echo "-> Registering Caelestia postHooks in $CLI_CONF..."
SYNC_CMD="python3 \"$SCRIPT_DIR/cursor_sync.py\""

python3 - "$SCRIPT_DIR" "$CLI_CONF" "$SYNC_CMD" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
from fsutil import atomic_json_dump, atomic_json_load

cli_path, sync_cmd = sys.argv[2], sys.argv[3]
config = atomic_json_load(cli_path, default={})
if not isinstance(config, dict):
    config = {}

for section in ("theme", "wallpaper"):
    section_config = config.setdefault(section, {})
    current = str(section_config.get("postHook", ""))
    if "cursor_sync.py" in current:
        continue
    section_config["postHook"] = f"{current}; {sync_cmd}" if current else sync_cmd

atomic_json_dump(cli_path, config, indent=4)
print("   Hooks registered in theme.postHook and wallpaper.postHook.")
PY

# 6. Remove the legacy inert integration symlink ---------------------------
LEGACY_LINK="${XDG_DATA_HOME:-$HOME/.local/share}/caelestia/plugins/caelestia-material-cursor"
if [ -L "$LEGACY_LINK" ]; then
    rm -f "$LEGACY_LINK"
    echo "   Removed legacy symlink: $LEGACY_LINK"
fi

# 7. Index wallpapers and run the initial sync -----------------------------
echo "-> Pre-indexing wallpaper database..."
python3 "$SCRIPT_DIR/cursor_sync.py" --scan

echo "-> Synchronizing initial cursor..."
python3 "$SCRIPT_DIR/cursor_sync.py"

echo "=========================================================="
echo "    Installation complete. Cursor synced."
echo "=========================================================="
