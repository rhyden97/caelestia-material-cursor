#!/usr/bin/env bash
# uninstall.sh — Revert caelestia-material-cursor integration.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/caelestia"
CLI_CONF="$CONFIG_DIR/cli.json"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/caelestia"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/caelestia"
ICONS_DIR="$HOME/.icons"
DATA_ICONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons"

PURGE=0
REMOVE_THEMES=0

usage() {
    cat <<'EOF'
Usage: ./uninstall.sh [OPTIONS]

Options:
  --purge          Also remove config, state and cached archives
  --themes         Also remove the Bibata-Material icon themes
  -h, --help       Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --purge) PURGE=1 ;;
        --themes) REMOVE_THEMES=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
    esac
    shift
done

echo "-> Removing Caelestia postHooks from $CLI_CONF..."
python3 - "$SCRIPT_DIR" "$CLI_CONF" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
from fsutil import atomic_json_dump, atomic_json_load

cli_path = sys.argv[2]
config = atomic_json_load(cli_path, default={})
if isinstance(config, dict):
    for section in ("theme", "wallpaper"):
        section_config = config.get(section)
        if not isinstance(section_config, dict):
            continue
        hook = str(section_config.get("postHook", ""))
        if "cursor_sync.py" not in hook:
            continue
        parts = [part.strip() for part in hook.split(";")]
        parts = [part for part in parts if "cursor_sync.py" not in part]
        if parts:
            section_config["postHook"] = "; ".join(parts)
        else:
            section_config.pop("postHook", None)
    atomic_json_dump(cli_path, config, indent=4)
    print("   Hooks removed.")
PY

LEGACY_LINK="${XDG_DATA_HOME:-$HOME/.local/share}/caelestia/plugins/caelestia-material-cursor"
if [ -L "$LEGACY_LINK" ]; then
    rm -f "$LEGACY_LINK"
    echo "   Removed legacy symlink: $LEGACY_LINK"
fi

if [ "$PURGE" -eq 1 ]; then
    rm -f "$CONFIG_DIR/cursor.json" "$CONFIG_DIR/cursor_plugin.json"
    rm -f "$STATE_DIR/cursor_state.json" "$STATE_DIR/wallpaper_cursors.json"
    rm -f "$STATE_DIR"/cursor_sync.*.lock "$STATE_DIR/.icons_flattened"
    rm -rf "$CACHE_DIR/cursor-themes" "$CACHE_DIR/cursor-thumbs"
    echo "   Purged config, state and cache."
fi

if [ "$REMOVE_THEMES" -eq 1 ]; then
    for theme_dir in "$ICONS_DIR"/Bibata-Material-*; do
        [ -e "$theme_dir" ] || continue
        rm -rf "$theme_dir"
    done
    for theme_dir in "$DATA_ICONS_DIR"/Bibata-Material-*; do
        [ -e "$theme_dir" ] || [ -L "$theme_dir" ] || continue
        rm -f "$theme_dir"
    done
    rm -rf "$ICONS_DIR/default" "$DATA_ICONS_DIR/default"
    echo "   Removed Bibata-Material icon themes."
fi

echo "Uninstall complete."
