#!/usr/bin/env python3
"""
cursor_sync.py — Core sync & caching engine for caelestia-material-cursor.

Synchronizes Hyprland, GTK, and system cursors with Caelestia's Material You colors.
Features:
- Sub-5ms instantaneous cursor switching using precomputed wallpaper database (wallpaper_cursors.json).
- Direct environment-variable ingestion from Caelestia postHook for zero-latency execution.
- Preserves exact color families across Dark/Light mode toggles (only toggling the -Light suffix).
- Background / CLI parallel batch wallpaper scanner with thread pool.
- Fixed 'Bibata-Material-Noir-Light' cursor pointer icon for desktop notifications.
- Automatic lazy icon flattening.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Local import
from color_match import find_closest_theme, extract_vibrant_color, djb2_hash


SCRIPT_DIR = Path(__file__).resolve().parent
THEMES_FILE = SCRIPT_DIR / "themes.json"
NOTIFICATION_ICON = SCRIPT_DIR / "assets" / "cursor_icon.png"
NOTIFICATION_ICON_SVG = SCRIPT_DIR / "assets" / "cursor_icon.svg"

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "caelestia"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "caelestia"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "caelestia"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "icons"
USER_ICONS_DIR = Path.home() / ".icons"

PLUGIN_CONFIG_FILE = CONFIG_DIR / "cursor_plugin.json"
CURSOR_STATE_FILE = STATE_DIR / "cursor_state.json"
WALLPAPER_CACHE_FILE = STATE_DIR / "wallpaper_cursors.json"
FLATTEN_FLAG_FILE = STATE_DIR / ".icons_flattened"
SCHEME_STATE_FILE = STATE_DIR / "scheme.json"
HYPR_VARS_FILE = CONFIG_DIR / "hypr-vars.lua"
HYPR_ENV_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "hypr" / "hyprland" / "env.lua"
LOCK_FILE = STATE_DIR / "cursor_sync.lock"

WALLPAPER_PATH_FILE = STATE_DIR / "wallpaper" / "path.txt"
WALLPAPER_CURRENT_SYMLINK = STATE_DIR / "wallpaper" / "current"
WALLPAPER_THUMB_FILE = STATE_DIR / "wallpaper" / "thumbnail.jpg"


def get_monitor_info() -> Dict[str, Any]:
    """Retrieves focused or primary monitor resolution and scale from Hyprland."""
    width, height, scale = 1920, 1080, 1.0
    name = "default"

    if shutil.which("hyprctl"):
        try:
            res = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, check=False)
            if res.returncode == 0 and res.stdout.strip():
                monitors = json.loads(res.stdout)
                if isinstance(monitors, list) and monitors:
                    target = next((m for m in monitors if m.get("focused")), monitors[0])
                    width = int(target.get("width", 1920))
                    height = int(target.get("height", 1080))
                    scale = float(target.get("scale", 1.0))
                    name = str(target.get("name", "eDP-1"))
        except Exception:
            pass

    return {
        "name": name,
        "width": width,
        "height": height,
        "scale": scale,
    }


def get_recommended_cursor_size() -> Tuple[int, str, List[Dict[str, Any]]]:
    """
    Calculates the recommended nominal cursor size and alternative options based on display configuration.
    Returns (recommended_size, rationale_string, options_list).
    """
    info = get_monitor_info()
    width, height, scale = info["width"], info["height"], info["scale"]

    if scale >= 1.75:
        # e.g. 4K UHD @ 2x scaling: nominal 24px renders at 48px physical pixels
        rec = 24
        reason = f"HiDPI scaled ({width}x{height} @ {scale}x): nominal 24px renders at {int(24 * scale)}px physical (crisp & standard)"
        options = [
            {"size": 24, "desc": f"24px (Recommended: renders at {int(24 * scale)}px physical)"},
            {"size": 28, "desc": f"28px (Slightly larger: renders at {int(28 * scale)}px physical)"},
            {"size": 32, "desc": f"32px (Large / high accessibility)"},
            {"size": 48, "desc": "48px (Extra Large)"},
        ]
    elif width >= 3400 and scale <= 1.25:
        # e.g. 4K UHD unscaled (@ 1x): 24px would be microscopic
        rec = 48
        reason = f"Unscaled 4K UHD ({width}x{height} @ {scale}x): nominal 48px ensures normal visibility"
        options = [
            {"size": 48, "desc": "48px (Recommended for unscaled 4K UHD)"},
            {"size": 36, "desc": "36px (Medium-large)"},
            {"size": 32, "desc": "32px (Compact)"},
            {"size": 24, "desc": "24px (Small)"},
        ]
    elif width >= 2400 and scale <= 1.25:
        # e.g. 1440p QHD unscaled
        rec = 28
        reason = f"1440p QHD ({width}x{height} @ {scale}x): nominal 28px provides optimal clarity"
        options = [
            {"size": 28, "desc": "28px (Recommended for 1440p QHD)"},
            {"size": 24, "desc": "24px (Standard 1080p size)"},
            {"size": 32, "desc": "32px (High visibility)"},
            {"size": 36, "desc": "36px (Large)"},
        ]
    else:
        # Standard 1080p FHD
        rec = 24
        reason = f"Standard resolution ({width}x{height} @ {scale}x): nominal 24px is standard Linux default"
        options = [
            {"size": 24, "desc": "24px (Recommended: standard Linux default)"},
            {"size": 28, "desc": "28px (Slightly larger, high visibility)"},
            {"size": 32, "desc": "32px (Large / accessible)"},
            {"size": 20, "desc": "20px (Compact)"},
        ]
    return rec, reason, options


def get_active_cursor_size() -> int:
    """Reads configured cursor size from hypr-vars.lua or plugin config, defaulting to recommended size."""
    if HYPR_VARS_FILE.exists():
        try:
            content = HYPR_VARS_FILE.read_text(encoding="utf-8")
            if match := re.search(r'cursorSize\s*=\s*(\d+)', content):
                return int(match.group(1))
        except Exception:
            pass

    cfg = load_plugin_config()
    if "size" in cfg and cfg["size"]:
        try:
            return int(cfg["size"])
        except Exception:
            pass

    rec_size, _, _ = get_recommended_cursor_size()
    return rec_size


def check_debounce(threshold_seconds: float = 0.5) -> bool:
    """Returns True if a sync occurred too recently (prevents duplicate triggers)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if LOCK_FILE.exists():
        try:
            last_run = float(LOCK_FILE.read_text().strip())
            if now - last_run < threshold_seconds:
                return True
        except Exception:
            pass
    try:
        LOCK_FILE.write_text(str(now))
    except Exception:
        pass
    return False


def ensure_flattened_icons(force: bool = False) -> None:
    """
    Moves or symlinks all Bibata-Material-* folders directly into ~/.icons and ~/.local/share/icons.
    Uses a state flag so it only runs once and does not slow down normal wallpaper changes.
    """
    if not force and FLATTEN_FLAG_FILE.exists():
        return

    USER_ICONS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for sub in USER_ICONS_DIR.glob("bibata-material-*"):
        if sub.is_dir():
            for theme_dir in sub.glob("Bibata-Material-*"):
                target = USER_ICONS_DIR / theme_dir.name
                if not target.exists():
                    try:
                        shutil.copytree(theme_dir, target, symlinks=True)
                    except Exception:
                        pass
                target_share = DATA_DIR / theme_dir.name
                if not target_share.exists():
                    try:
                        shutil.copytree(theme_dir, target_share, symlinks=True)
                    except Exception:
                        pass

    try:
        FLATTEN_FLAG_FILE.write_text("1")
    except Exception:
        pass


def get_default_config() -> Dict[str, Any]:
    return {
        "enabled": True,
        "size": 24,
        "notify": True,
        "overrideTheme": None,
    }


def load_plugin_config() -> Dict[str, Any]:
    if PLUGIN_CONFIG_FILE.exists():
        try:
            with open(PLUGIN_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                defaults = get_default_config()
                defaults.update(cfg)
                return defaults
        except Exception as e:
            print(f"[caelestia-material-cursor] Warning: Error loading config ({e}), using defaults.", file=sys.stderr)
    return get_default_config()


def save_plugin_config(cfg: Dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(PLUGIN_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def load_cursor_state() -> Dict[str, Any]:
    if CURSOR_STATE_FILE.exists():
        try:
            with open(CURSOR_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_cursor_state(state: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(CURSOR_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def load_wallpaper_cache() -> Dict[str, Dict[str, Any]]:
    """Loads precomputed wallpaper -> color family database from disk."""
    if WALLPAPER_CACHE_FILE.exists():
        try:
            with open(WALLPAPER_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_wallpaper_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(WALLPAPER_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass


def get_current_wallpaper_path() -> Optional[str]:
    """Reads current wallpaper file path from postHook environment or Caelestia state."""
    # 1. Environment variable passed by Caelestia postHook (0ms)
    if env_wall := os.environ.get("WALLPAPER_PATH"):
        return env_wall

    # 2. Symlink or state file
    if WALLPAPER_CURRENT_SYMLINK.exists():
        try:
            resolved = WALLPAPER_CURRENT_SYMLINK.resolve()
            return str(resolved)
        except Exception:
            pass

    if WALLPAPER_PATH_FILE.exists():
        try:
            path_str = WALLPAPER_PATH_FILE.read_text(encoding="utf-8").strip()
            if path_str:
                return path_str
        except Exception:
            pass

    return None


def get_wallpaper_thumbnail(wall_path_str: Optional[str] = None) -> Optional[Path]:
    """Returns the Caelestia thumbnail if available, or the wallpaper file."""
    if env_thumb := os.environ.get("THUMBNAIL_PATH"):
        p = Path(env_thumb)
        if p.exists():
            return p

    if WALLPAPER_THUMB_FILE.exists():
        return WALLPAPER_THUMB_FILE

    target_str = wall_path_str or get_current_wallpaper_path()
    if not target_str:
        return None

    target = Path(target_str)
    if target.suffix.lower() in {".mp4", ".webm", ".mkv"}:
        h = djb2_hash(str(target.resolve()))
        video_thumb = CACHE_DIR / "videothumbs" / f"{h}.jpg"
        if video_thumb.exists():
            return video_thumb

    if target.exists():
        return target

    return None


def get_caelestia_scheme() -> Dict[str, Any]:
    """Reads Caelestia's current scheme and colours from postHook env, state file or CLI."""
    # 1. Direct environment variable lookup (0ms)
    env_mode = os.environ.get("SCHEME_MODE")
    env_name = os.environ.get("SCHEME_NAME")
    env_colours = os.environ.get("SCHEME_COLOURS")
    if env_mode and env_name:
        colours = {}
        if env_colours:
            try:
                colours = json.loads(env_colours)
            except Exception:
                pass
        return {
            "name": env_name,
            "mode": env_mode,
            "colours": colours
        }

    # 2. State file lookup (<1ms)
    if SCHEME_STATE_FILE.exists():
        try:
            with open(SCHEME_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # 3. Fallback CLI lookup
    try:
        out = subprocess.check_output(["caelestia", "scheme", "get"], text=True)
        return json.loads(out)
    except Exception:
        pass

    return {
        "name": "dynamic",
        "mode": "dark",
        "colours": {
            "primary": "90caf9",
            "surface": "121212"
        }
    }


def is_theme_installed(theme_name: str) -> bool:
    """Checks whether the theme directory exists in any common icon path."""
    check_dirs = [
        USER_ICONS_DIR / theme_name,
        DATA_DIR / theme_name,
        Path("/usr/share/icons") / theme_name,
        Path("/usr/local/share/icons") / theme_name,
    ]
    return any(p.exists() for p in check_dirs)


def update_hypr_vars_lua(theme_name: str, size: int) -> None:
    """Safely updates cursorTheme and cursorSize in ~/.config/caelestia/hypr-vars.lua."""
    if not HYPR_VARS_FILE.exists():
        return

    try:
        content = HYPR_VARS_FILE.read_text(encoding="utf-8")

        # Update cursorTheme
        if re.search(r'cursorTheme\s*=', content):
            content = re.sub(
                r'cursorTheme\s*=\s*["\'][^"\']*["\']',
                f'cursorTheme = "{theme_name}"',
                content
            )
        else:
            content = re.sub(
                r'(return\s*\{)',
                f'\\1\n  cursorTheme = "{theme_name}",',
                content
            )

        # Update cursorSize
        if re.search(r'cursorSize\s*=', content):
            content = re.sub(
                r'cursorSize\s*=\s*\d+',
                f'cursorSize = {size}',
                content
            )
        else:
            content = re.sub(
                r'(return\s*\{)',
                f'\\1\n  cursorSize = {size},',
                content
            )

        HYPR_VARS_FILE.write_text(content, encoding="utf-8")
    except Exception as e:
        print(f"[caelestia-material-cursor] Failed to update hypr-vars.lua: {e}", file=sys.stderr)


def ensure_hypr_env_lua() -> None:
    """Ensures HYPRCURSOR_THEME and HYPRCURSOR_SIZE are exported in ~/.config/hypr/hyprland/env.lua for instant boot sync."""
    if not HYPR_ENV_FILE.exists():
        return
    try:
        content = HYPR_ENV_FILE.read_text(encoding="utf-8")
        if "HYPRCURSOR_THEME" not in content:
            pattern = r'hl\.env\(["\']XCURSOR_THEME["\'],\s*vars\.cursorTheme\)'
            if re.search(pattern, content):
                content = re.sub(
                    pattern,
                    'hl.env("HYPRCURSOR_THEME", vars.cursorTheme)\nhl.env("HYPRCURSOR_SIZE", vars.cursorSize)\n\\g<0>',
                    content,
                    count=1
                )
            else:
                content += '\nhl.env("HYPRCURSOR_THEME", vars.cursorTheme)\nhl.env("HYPRCURSOR_SIZE", vars.cursorSize)\n'
            HYPR_ENV_FILE.write_text(content, encoding="utf-8")
    except Exception:
        pass


def update_default_cursor(theme_name: str, size: int) -> None:
    """
    Updates default cursor pointers in ~/.icons/default and ~/.local/share/icons/default:
    - Writes index.theme (Inherits={theme_name}) for legacy X11 & GTK.
    - Writes manifest.hl for native Hyprcursor fallback.
    - Symlinks hyprcursors and cursors directly to the active theme.
    Ensures zero delay or color mismatch on reboot, login, or empty desktop.
    """
    xcursor_content = (
        "[Icon Theme]\n"
        f"Name=Default\n"
        f"Comment=Default Cursor Theme\n"
        f"Inherits={theme_name}\n"
    )
    hypr_manifest_content = (
        "name = Default\n"
        "description = Default Hyprcursor Theme\n"
        "version = 0.1\n"
        "cursors_directory = hyprcursors\n"
    )
    for base_dir in (USER_ICONS_DIR, DATA_DIR):
        try:
            default_dir = base_dir / "default"
            default_dir.mkdir(parents=True, exist_ok=True)
            (default_dir / "index.theme").write_text(xcursor_content, encoding="utf-8")
            (default_dir / "manifest.hl").write_text(hypr_manifest_content, encoding="utf-8")

            src_theme = base_dir / theme_name
            if src_theme.exists():
                for sub in ("cursors", "hyprcursors"):
                    target_sub = default_dir / sub
                    if target_sub.is_symlink() or target_sub.is_file():
                        target_sub.unlink()
                    elif target_sub.is_dir():
                        shutil.rmtree(target_sub)
                    src_sub = src_theme / sub
                    if src_sub.exists():
                        target_sub.symlink_to(f"../{theme_name}/{sub}")
        except Exception:
            pass


def get_notification_icon() -> str:
    """Returns the fixed Bibata-Material-Noir-Light icon path for notifications."""
    if NOTIFICATION_ICON.exists():
        return str(NOTIFICATION_ICON)
    if NOTIFICATION_ICON_SVG.exists():
        return str(NOTIFICATION_ICON_SVG)
    return "preferences-desktop-theme"


def apply_cursor(theme_name: str, size: int) -> None:
    """
    Applies the cursor immediately to Hyprland, GTK/GSettings, dconf, and config files.
    Ensures immediate hardware cursor buffer reload even on an empty desktop with no windows.
    """
    # 1. Update persistent Caelestia hypr-vars.lua file first
    update_hypr_vars_lua(theme_name, size)

    # 2. Ensure Hyprland env.lua exports HYPRCURSOR_THEME on startup
    ensure_hypr_env_lua()

    # 3. Environment & Legacy X11/Hyprcursor fallbacks in default/
    update_default_cursor(theme_name, size)

    # 4. Update Hyprland environment & runtime Lua configuration state
    lua_sync_script = (
        f"hl.env('HYPRCURSOR_THEME', '{theme_name}'); "
        f"hl.env('HYPRCURSOR_SIZE', '{size}'); "
        f"hl.env('XCURSOR_THEME', '{theme_name}'); "
        f"hl.env('XCURSOR_SIZE', '{size}'); "
        "package.loaded['hypr-vars'] = nil; "
        f"local f = io.open('{HYPR_VARS_FILE}'); "
        "if f then f:close(); "
        f"local o = dofile('{HYPR_VARS_FILE}'); "
        "local v = require('variables'); "
        "for k, val in pairs(o) do v[k] = val end end"
    )
    try:
        subprocess.run(["hyprctl", "eval", lua_sync_script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    # 5. Hyprland compositor: Set cursor
    try:
        subprocess.run(["hyprctl", "setcursor", theme_name, str(size)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    # 6. GTK / GSettings & dconf
    if shutil.which("gsettings"):
        try:
            subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", "cursor-theme", theme_name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", "cursor-size", str(size)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    if shutil.which("dconf"):
        try:
            subprocess.run(["dconf", "write", "/org/gnome/desktop/interface/cursor-theme", f"'{theme_name}'"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["dconf", "write", "/org/gnome/desktop/interface/cursor-size", str(size)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass



def filter_xcursor(data: bytes, target_sizes: set[int] = {24}) -> bytes:
    """Filters an XCursor binary to only retain images matching target_sizes."""
    magic = data[:4]
    if magic != b'Xcur':
        return data
    header_len, version, ntoc = struct.unpack('<III', data[4:16])
    matching_chunks = []
    for i in range(ntoc):
        ctype, subtype, pos = struct.unpack('<III', data[16 + i*12 : 28 + i*12])
        if pos + 36 > len(data):
            continue
        c_header_len, c_type, c_subtype, c_version, c_width, c_height, c_xhot, c_yhot, c_delay = struct.unpack('<IIIIIIIII', data[pos:pos+36])
        chunk_len = 36 + c_width * c_height * 4
        if pos + chunk_len > len(data):
            continue
        chunk_bytes = data[pos : pos + chunk_len]
        if subtype in target_sizes:
            matching_chunks.append((ctype, subtype, chunk_bytes))

    if not matching_chunks:
        return data

    new_ntoc = len(matching_chunks)
    new_header = b'Xcur' + struct.pack('<III', header_len, version, new_ntoc)
    toc_offset = 16 + new_ntoc * 12
    new_toc = bytearray()
    new_body = bytearray()
    curr_pos = toc_offset
    for ctype, subtype, chunk_bytes in matching_chunks:
        new_toc += struct.pack('<III', ctype, subtype, curr_pos)
        new_body += chunk_bytes
        curr_pos += len(chunk_bytes)
    return bytes(new_header + new_toc + new_body)


def optimize_theme_directory(theme_dir: Path, target_sizes: set[int] = {24}) -> Tuple[int, int]:
    """
    Optimizes a single theme directory:
    - Retains vector hyprcursors/ intact.
    - Strips unused nominal sizes from cursors/ XCursor binaries, keeping only target_sizes.
    Returns (files_modified, bytes_saved).
    """
    cursors_dir = theme_dir / "cursors"
    if not cursors_dir.exists() or not cursors_dir.is_dir():
        return 0, 0

    files_modified = 0
    bytes_saved = 0

    for f in cursors_dir.iterdir():
        if f.is_file() and not f.is_symlink():
            try:
                orig_data = f.read_bytes()
                filtered = filter_xcursor(orig_data, target_sizes)
                if len(filtered) < len(orig_data):
                    bytes_saved += (len(orig_data) - len(filtered))
                    f.write_bytes(filtered)
                    files_modified += 1
            except Exception:
                pass

    return files_modified, bytes_saved


def optimize_storage(target_size: Optional[int] = None, clean_archives: bool = True) -> Dict[str, Any]:
    """
    Optimizes disk storage for Bibata cursor themes:
    1. Removes redundant extracted archive directories (bibata-material-*).
    2. Strips unused nominal sizes from all installed themes in ~/.icons and ~/.local/share/icons.
    """
    if target_size is None or target_size <= 0:
        target_size, _, _ = get_recommended_cursor_size()

    target_sizes = {target_size}
    total_saved = 0
    cleaned_folders = []
    themes_optimized = 0

    # 1. Clean redundant extraction folders
    if clean_archives:
        for p in USER_ICONS_DIR.glob("bibata-material-*"):
            if p.is_dir():
                folder_size = sum(f.stat().st_size for f in p.rglob('*') if f.is_file() and not f.is_symlink())
                try:
                    shutil.rmtree(p)
                    total_saved += folder_size
                    cleaned_folders.append(p.name)
                except Exception:
                    pass

    # 2. Optimize installed themes
    seen_themes = set()
    for base_dir in (USER_ICONS_DIR, DATA_DIR):
        if not base_dir.exists():
            continue
        for theme_dir in base_dir.glob("Bibata-Material-*"):
            if theme_dir.is_dir() and not theme_dir.is_symlink():
                resolved = theme_dir.resolve()
                if resolved in seen_themes:
                    continue
                seen_themes.add(resolved)

                _, saved = optimize_theme_directory(theme_dir, target_sizes)
                if saved > 0:
                    total_saved += saved
                    themes_optimized += 1

    return {
        "target_size": target_size,
        "themes_optimized": themes_optimized,
        "cleaned_archive_folders": cleaned_folders,
        "total_mb_saved": round(total_saved / (1024 * 1024), 2),
    }


def process_single_wallpaper(file_path: Path, themes: Dict[str, Any]) -> Tuple[str, str, float]:
    """Processes a single static image or video wallpaper and returns (clean_path, family, mtime)."""
    try:
        clean_path = str(file_path.resolve())
        mtime = file_path.stat().st_mtime
    except OSError:
        return str(file_path), "Classic", 0.0

    # Video wallpaper check
    if file_path.suffix.lower() in {".mp4", ".webm", ".mkv"}:
        h = djb2_hash(clean_path)
        video_thumb = CACHE_DIR / "videothumbs" / f"{h}.jpg"
        if video_thumb.exists():
            family, _, _ = extract_vibrant_color(video_thumb, themes)
            return clean_path, family, mtime

        temp_thumb = Path(f"/tmp/cursor_thumb_{h}.jpg")
        try:
            subprocess.run([
                "ffmpeg", "-y", "-ss", "00:00:01", "-i", clean_path,
                "-frames:v", "1", "-q:v", "2", str(temp_thumb)
            ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if temp_thumb.exists():
                family, _, _ = extract_vibrant_color(temp_thumb, themes)
                temp_thumb.unlink(missing_ok=True)
                return clean_path, family, mtime
        except Exception:
            pass
        return clean_path, "Soft-Blue", mtime

    # Static image wallpaper
    family, _, _ = extract_vibrant_color(file_path, themes)
    return clean_path, family, mtime


def scan_all_wallpapers(wallpapers_dir: Optional[Path] = None, notify: bool = True) -> int:
    """
    Scans all wallpapers in ~/Pictures/Wallpapers (and Animated),
    computes their vibrant cursor family in parallel, and saves to wallpaper_cursors.json.
    """
    if wallpapers_dir is None:
        wallpapers_dir = Path.home() / "Pictures" / "Wallpapers"

    if not wallpapers_dir.exists():
        print(f"[caelestia-material-cursor] Wallpapers directory not found: {wallpapers_dir}", file=sys.stderr)
        return 0

    ensure_flattened_icons()

    with open(THEMES_FILE, "r", encoding="utf-8") as f:
        themes = json.load(f)

    valid_extensions = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".svg", ".gif", ".mp4", ".webm", ".mkv"}
    all_files = [f for f in wallpapers_dir.rglob("*") if f.is_file() and f.suffix.lower() in valid_extensions]

    if not all_files:
        print("[caelestia-material-cursor] No wallpapers found to scan.")
        return 0

    cache = load_wallpaper_cache()
    files_to_process = []

    for f in all_files:
        try:
            resolved_str = str(f.resolve())
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if resolved_str in cache and cache[resolved_str].get("mtime") == mtime:
            continue
        files_to_process.append(f)

    count_new = len(files_to_process)
    if count_new > 0:
        print(f"[caelestia-material-cursor] Scanning {count_new} new/modified wallpapers in parallel...")
        workers = min(16, (os.cpu_count() or 4) * 2)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for path_str, family, mtime in executor.map(lambda p: process_single_wallpaper(p, themes), files_to_process):
                if mtime > 0:
                    cache[path_str] = {
                        "family": family,
                        "mtime": mtime
                    }

        save_wallpaper_cache(cache)
        print(f"[caelestia-material-cursor] Finished! Total wallpapers in database: {len(cache)}")
    else:
        print(f"[caelestia-material-cursor] Database already up to date ({len(cache)} wallpapers cached).")

    if notify and shutil.which("notify-send"):
        try:
            icon_arg = get_notification_icon()
            subprocess.run([
                "notify-send",
                "-a", "Caelestia",
                "-i", icon_arg,
                "Wallpaper Database Updated",
                f"{len(cache)} wallpapers indexed ({count_new} new added)."
            ], check=False)
        except Exception:
            pass

    return len(cache)


def sync(force_recalculate: bool = False) -> None:
    """Main synchronization logic with sub-5ms cached execution."""
    if not force_recalculate and check_debounce():
        return

    # Ensure icon folders are available (fast once-only check)
    ensure_flattened_icons()

    config = load_plugin_config()
    if not config.get("enabled", True):
        return

    cursor_size = get_active_cursor_size()
    override = config.get("overrideTheme")

    if override:
        apply_cursor(override, cursor_size)
        return

    if not THEMES_FILE.exists():
        print(f"[caelestia-material-cursor] Error: {THEMES_FILE} not found.", file=sys.stderr)
        return

    with open(THEMES_FILE, "r", encoding="utf-8") as f:
        themes = json.load(f)

    caelestia_data = get_caelestia_scheme()
    mode = caelestia_data.get("mode", "dark").lower()
    scheme_name = caelestia_data.get("name", "dynamic").lower()
    colours = caelestia_data.get("colours", {})

    current_wall = get_current_wallpaper_path()
    last_state = load_cursor_state()
    last_wall = last_state.get("wallpaper")
    last_family = last_state.get("family")

    family: str

    if scheme_name != "dynamic":
        # Static preset scheme (e.g. catppuccin, gruvbox)
        primary_hex = colours.get("primary") or colours.get("m3primary", "90caf9")
        if not primary_hex.startswith("#"):
            primary_hex = f"#{primary_hex}"
        family, _ = find_closest_theme(primary_hex, themes, kL=2.0)

    elif not force_recalculate and current_wall == last_wall and last_family:
        # Wallpaper has not changed -> PRESERVE EXACT COLOR FAMILY (only toggle Dark <-> Light)
        family = last_family

    else:
        # Wallpaper changed or first run: check fast precomputed database
        cache = load_wallpaper_cache()
        cached_entry = cache.get(current_wall) if current_wall else None

        if cached_entry and not force_recalculate:
            # 0ms Instant Cache Hit!
            family = cached_entry.get("family", "Soft-Blue")
        else:
            # Cache miss or forced recalc: extract on the fly and store
            thumb_path = get_wallpaper_thumbnail(current_wall)
            if thumb_path and thumb_path.exists():
                family, hex_code, delta_e = extract_vibrant_color(thumb_path, themes)
            else:
                primary_hex = colours.get("primary") or colours.get("m3primary", "90caf9")
                if not primary_hex.startswith("#"):
                    primary_hex = f"#{primary_hex}"
                family, _ = find_closest_theme(primary_hex, themes, kL=2.0)

            # Store in cache for future instant lookups
            if current_wall and Path(current_wall).exists():
                cache[current_wall] = {
                    "family": family,
                    "mtime": Path(current_wall).stat().st_mtime
                }
                save_wallpaper_cache(cache)

    # Construct target theme name
    if family == "Classic":
        target_theme = "Bibata-Material-Classic"
    elif mode == "light":
        target_theme = f"Bibata-Material-{family}-Light"
    else:
        target_theme = f"Bibata-Material-{family}"

    # Verify theme installation with fallback
    if not is_theme_installed(target_theme):
        if is_theme_installed(f"Bibata-Material-{family}"):
            target_theme = f"Bibata-Material-{family}"
        elif is_theme_installed("Bibata-Material-Classic"):
            target_theme = "Bibata-Material-Classic"
        else:
            print(f"[caelestia-material-cursor] Warning: Theme '{target_theme}' not found.", file=sys.stderr)

    # Apply cursor immediately (silent, no notification on wallpaper changes)
    apply_cursor(target_theme, cursor_size)

    # Persist state
    save_cursor_state({
        "family": family,
        "mode": mode,
        "wallpaper": current_wall,
        "scheme_name": scheme_name,
        "applied_theme": target_theme,
    })


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        print("Caelestia Material Cursor Sync CLI")
        print("Usage: python3 cursor_sync.py [OPTION]\n")
        print("Options:")
        print("  (no args)             Synchronize cursor with current wallpaper & color scheme")
        print("  --scan                Rescan all wallpapers in ~/Pictures/Wallpapers in parallel")
        print("  --optimize [SIZE]     Strip unused cursor sizes (default: auto-detected) and delete duplicate archives")
        print("  --detect-size         Detect screen resolution & scale, and display recommended cursor sizes")
        print("  --status              Print current state, configuration, cache stats, and display info")
        print("  --toggle              Toggle automatic cursor synchronization on/off")
        print("  --recalc              Force color extraction without using wallpaper cache")
        print("  --help, -h            Display this help message")
    elif len(sys.argv) > 1 and sys.argv[1] == "--detect-size":
        rec, reason, options = get_recommended_cursor_size()
        mon = get_monitor_info()
        print(json.dumps({
            "name": mon["name"],
            "width": mon["width"],
            "height": mon["height"],
            "scale": mon["scale"],
            "recommended_size": rec,
            "reason": reason,
            "options": options
        }, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "--status":
        cfg = load_plugin_config()
        state = load_cursor_state()
        cache = load_wallpaper_cache()
        rec, reason, _ = get_recommended_cursor_size()
        mon = get_monitor_info()
        active_size = get_active_cursor_size()

        installed_themes = 0
        total_bytes = 0
        seen_dirs = set()
        for base_dir in (USER_ICONS_DIR, DATA_DIR):
            if not base_dir.exists():
                continue
            for t in base_dir.glob("Bibata-Material-*"):
                if t.is_dir() and not t.is_symlink():
                    resolved = t.resolve()
                    if resolved in seen_dirs:
                        continue
                    seen_dirs.add(resolved)
                    installed_themes += 1
                    total_bytes += sum(f.stat().st_size for f in t.rglob("*") if f.is_file() and not f.is_symlink())

        print(json.dumps({
            "display": {
                "monitor": mon["name"],
                "resolution": f"{mon['width']}x{mon['height']}",
                "scale": f"{mon['scale']}x",
                "recommended_cursor_size": f"{rec}px",
                "recommendation_rationale": reason
            },
            "active_cursor": {
                "theme": state.get("applied_theme", "Bibata-Material-Classic"),
                "family": state.get("family", "Classic"),
                "mode": state.get("mode", "dark"),
                "size": f"{active_size}px"
            },
            "storage": {
                "installed_themes_count": installed_themes,
                "disk_usage_mb": round(total_bytes / (1024 * 1024), 2)
            },
            "config": cfg,
            "cached_wallpapers_count": len(cache)
        }, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "--scan":
        scan_all_wallpapers(notify=True)
    elif len(sys.argv) > 1 and sys.argv[1] == "--optimize":
        target_size = None
        if len(sys.argv) > 2:
            try:
                target_size = int(sys.argv[2])
            except ValueError:
                pass
        rec, _, _ = get_recommended_cursor_size()
        eff_size = target_size or rec
        print(f"Optimizing theme storage for target size {eff_size}px...")
        res = optimize_storage(target_size=eff_size)
        print(f"Done! Optimized {res['themes_optimized']} themes, freed {res['total_mb_saved']} MB of disk space.")
        if res['cleaned_archive_folders']:
            print(f"Cleaned {len(res['cleaned_archive_folders'])} redundant archive extract directories.")
    elif len(sys.argv) > 1 and sys.argv[1] == "--toggle":
        cfg = load_plugin_config()
        cfg["enabled"] = not cfg.get("enabled", True)
        save_plugin_config(cfg)
        print(f"Cursor sync enabled: {cfg['enabled']}")
        if cfg["enabled"]:
            sync()
    elif len(sys.argv) > 1 and sys.argv[1] == "--recalc":
        sync(force_recalculate=True)
    else:
        sync()
