#!/usr/bin/env python3
"""cursor_sync.py — Core sync & caching engine for caelestia-material-cursor.

Synchronizes Hyprland, GTK and system cursors with Caelestia's Material You
colours. Features:
- O(1) wallpaper -> colour-family lookup with mtime+size invalidation.
- Direct environment ingestion from the Caelestia postHooks.
- Colour-family preservation across Dark/Light toggles.
- Parallel wallpaper scanner with pruning.
- Reversible XCursor optimization (``--optimize`` / ``--restore``).
- Atomic writes for every state/config file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from color_match import djb2_hash, extract_vibrant_color, find_closest_theme
from fsutil import atomic_json_dump, atomic_write

SCRIPT_DIR = Path(__file__).resolve().parent
THEMES_FILE = SCRIPT_DIR / "themes.json"

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "caelestia"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "caelestia"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "caelestia"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "icons"
USER_ICONS_DIR = Path.home() / ".icons"

CONFIG_FILE = CONFIG_DIR / "cursor.json"
LEGACY_CONFIG_FILE = CONFIG_DIR / "cursor_plugin.json"
CURSOR_STATE_FILE = STATE_DIR / "cursor_state.json"
WALLPAPER_CACHE_FILE = STATE_DIR / "wallpaper_cursors.json"
FLATTEN_FLAG_FILE = STATE_DIR / ".icons_flattened"
SCHEME_STATE_FILE = STATE_DIR / "scheme.json"
HYPR_VARS_FILE = CONFIG_DIR / "hypr-vars.lua"
HYPR_ENV_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "hypr" / "hyprland" / "env.lua"

WALLPAPER_PATH_FILE = STATE_DIR / "wallpaper" / "path.txt"
WALLPAPER_CURRENT_SYMLINK = STATE_DIR / "wallpaper" / "current"
WALLPAPER_THUMB_FILE = STATE_DIR / "wallpaper" / "thumbnail.jpg"

THEME_CACHE_DIR = CACHE_DIR / "cursor-themes"
THUMB_TMP_DIR = CACHE_DIR / "cursor-thumbs"
TRASH_DIR = THEME_CACHE_DIR / "trash"

WALLPAPER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".svg", ".gif", ".mp4", ".webm", ".mkv"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv"}
THEME_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MIN_CURSOR_SIZE = 16
MAX_CURSOR_SIZE = 96
CONFIG_KEYS = ("enabled", "size", "overrideTheme")


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def validate_theme_name(name: Any) -> str:
    """Return a safe cursor theme name or raise ValueError."""
    if not isinstance(name, str) or not THEME_NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid cursor theme name: {name!r}")
    return name


def validate_size(size: Any) -> int:
    """Return a cursor size inside the supported range or raise ValueError."""
    try:
        value = int(size)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid cursor size: {size!r}") from exc
    if not MIN_CURSOR_SIZE <= value <= MAX_CURSOR_SIZE:
        raise ValueError(
            f"Cursor size must be between {MIN_CURSOR_SIZE} and {MAX_CURSOR_SIZE} (got {value})"
        )
    return value


# --------------------------------------------------------------------------- #
# Display detection
# --------------------------------------------------------------------------- #

def get_monitor_info() -> dict[str, Any]:
    """Retrieve the focused (or primary) monitor resolution and scale from Hyprland."""
    width, height, scale = 1920, 1080, 1.0
    name = "default"

    if shutil.which("hyprctl"):
        try:
            res = subprocess.run(
                ["hyprctl", "monitors", "-j"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                monitors = json.loads(res.stdout)
                if isinstance(monitors, list) and monitors:
                    target = next((m for m in monitors if m.get("focused")), monitors[0])
                    width = int(target.get("width", 1920))
                    height = int(target.get("height", 1080))
                    scale = float(target.get("scale", 1.0))
                    name = str(target.get("name", "eDP-1"))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass

    return {"name": name, "width": width, "height": height, "scale": scale}


def get_recommended_cursor_size() -> tuple[int, str, list[dict[str, Any]]]:
    """Return (recommended_size, rationale, options) for the current display."""
    info = get_monitor_info()
    width, height, scale = info["width"], info["height"], info["scale"]

    if scale >= 1.75:
        rec = 24
        reason = (
            f"HiDPI scaled ({width}x{height} @ {scale}x): nominal 24px renders at "
            f"{int(24 * scale)}px physical (crisp & standard)"
        )
        options = [
            {"size": 24, "desc": f"24px (Recommended: renders at {int(24 * scale)}px physical)"},
            {"size": 28, "desc": f"28px (Slightly larger: renders at {int(28 * scale)}px physical)"},
            {"size": 32, "desc": "32px (Large / high accessibility)"},
            {"size": 48, "desc": "48px (Extra Large)"},
        ]
    elif width >= 3400 and scale <= 1.25:
        rec = 48
        reason = f"Unscaled 4K UHD ({width}x{height} @ {scale}x): nominal 48px ensures normal visibility"
        options = [
            {"size": 48, "desc": "48px (Recommended for unscaled 4K UHD)"},
            {"size": 36, "desc": "36px (Medium-large)"},
            {"size": 32, "desc": "32px (Compact)"},
            {"size": 24, "desc": "24px (Small)"},
        ]
    elif width >= 2400 and scale <= 1.25:
        rec = 28
        reason = f"1440p QHD ({width}x{height} @ {scale}x): nominal 28px provides optimal clarity"
        options = [
            {"size": 28, "desc": "28px (Recommended for 1440p QHD)"},
            {"size": 24, "desc": "24px (Standard 1080p size)"},
            {"size": 32, "desc": "32px (High visibility)"},
            {"size": 36, "desc": "36px (Large)"},
        ]
    else:
        rec = 24
        reason = f"Standard resolution ({width}x{height} @ {scale}x): nominal 24px is standard Linux default"
        options = [
            {"size": 24, "desc": "24px (Recommended: standard Linux default)"},
            {"size": 28, "desc": "28px (Slightly larger, high visibility)"},
            {"size": 32, "desc": "32px (Large / accessible)"},
            {"size": 20, "desc": "20px (Compact)"},
        ]
    return rec, reason, options


def get_recommended_target_sizes(size: int) -> set[int]:
    """Raster sizes worth keeping: nominal size plus its physical HiDPI twin."""
    scale = get_monitor_info()["scale"]
    return {size, min(MAX_CURSOR_SIZE, max(size, int(round(size * scale))))}


# --------------------------------------------------------------------------- #
# Config / state
# --------------------------------------------------------------------------- #

def get_default_config() -> dict[str, Any]:
    return {"enabled": True, "size": 24, "overrideTheme": None}


def load_config() -> dict[str, Any]:
    """Load the config, migrating the legacy filename and dropping unknown keys."""
    source = CONFIG_FILE if CONFIG_FILE.exists() else LEGACY_CONFIG_FILE
    defaults = get_default_config()
    if source.exists():
        try:
            with open(source, encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict):
                defaults.update({key: value for key, value in raw.items() if key in CONFIG_KEYS})
        except (OSError, ValueError) as exc:
            print(
                f"[caelestia-material-cursor] Warning: cannot read config ({exc}); using defaults.",
                file=sys.stderr,
            )
    return defaults


def save_config(config: dict[str, Any]) -> None:
    atomic_json_dump(CONFIG_FILE, {key: config.get(key) for key in CONFIG_KEYS})


def load_cursor_state() -> dict[str, Any]:
    try:
        with open(CURSOR_STATE_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cursor_state(state: dict[str, Any]) -> None:
    atomic_json_dump(CURSOR_STATE_FILE, state)


def load_wallpaper_cache() -> dict[str, dict[str, Any]]:
    try:
        with open(WALLPAPER_CACHE_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_wallpaper_cache(cache: dict[str, dict[str, Any]]) -> None:
    atomic_json_dump(WALLPAPER_CACHE_FILE, cache)


def get_size_from_hypr_vars() -> int | None:
    if not HYPR_VARS_FILE.exists():
        return None
    try:
        content = HYPR_VARS_FILE.read_text(encoding="utf-8")
        match = re.search(r"cursorSize\s*=\s*(\d+)", content)
        if match:
            return int(match.group(1))
    except (OSError, ValueError):
        pass
    return None


def get_active_cursor_size() -> tuple[int, str]:
    """Resolve cursor size: config > hypr-vars > auto-detected."""
    config = load_config()
    raw = config.get("size")
    if raw is not None:
        try:
            return validate_size(raw), "config"
        except ValueError:
            pass

    from_hypr = get_size_from_hypr_vars()
    if from_hypr is not None:
        try:
            return validate_size(from_hypr), "hypr-vars"
        except ValueError:
            pass

    return get_recommended_cursor_size()[0], "auto"


# --------------------------------------------------------------------------- #
# Debounce
# --------------------------------------------------------------------------- #

def infer_event() -> str:
    return "wallpaper" if os.environ.get("WALLPAPER_PATH") else "theme"


def check_debounce(event: str = "sync", threshold_seconds: float = 0.3) -> bool:
    """Return True when the same event fired too recently (duplicate hook)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = STATE_DIR / f"cursor_sync.{event}.lock"
    now = time.time()
    if lock_file.exists():
        try:
            if now - float(lock_file.read_text().strip()) < threshold_seconds:
                return True
        except (OSError, ValueError):
            pass
    try:
        atomic_write(lock_file, str(now))
    except OSError:
        pass
    return False


# --------------------------------------------------------------------------- #
# Icon theme helpers
# --------------------------------------------------------------------------- #

def _link_or_copy(source: Path, target: Path) -> None:
    """Create a symlink to *source*, falling back to a real copy."""
    if target.exists() or target.is_symlink():
        return
    try:
        target.symlink_to(source, target_is_directory=True)
    except OSError:
        shutil.copytree(source, target, symlinks=True)


def _replace_with_link(source: Path, target: Path) -> None:
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    _link_or_copy(source, target)


def _dir_signature(path: Path) -> tuple[int, int]:
    total = 0
    count = 0
    for item in path.rglob("*"):
        if item.is_file() and not item.is_symlink():
            total += item.stat().st_size
            count += 1
    return count, total


def ensure_flattened_icons(force: bool = False) -> None:
    """Expose every theme in ~/.icons and link it from the XDG data directory."""
    if not force and FLATTEN_FLAG_FILE.exists():
        return

    USER_ICONS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for sub in USER_ICONS_DIR.glob("bibata-material-*"):
        if not sub.is_dir() or sub.is_symlink():
            continue
        for theme_dir in sub.glob("Bibata-Material-*"):
            if theme_dir.is_dir():
                _link_or_copy(theme_dir, USER_ICONS_DIR / theme_dir.name)
                _link_or_copy(theme_dir, DATA_DIR / theme_dir.name)

    for theme_dir in USER_ICONS_DIR.glob("Bibata-Material-*"):
        if theme_dir.is_dir() and not theme_dir.is_symlink():
            _link_or_copy(theme_dir, DATA_DIR / theme_dir.name)

    try:
        atomic_write(FLATTEN_FLAG_FILE, "1")
    except OSError:
        pass


def dedupe_icon_dirs() -> list[str]:
    """Replace duplicate real copies in the data dir with symlinks (explicit command)."""
    deduped: list[str] = []
    if not DATA_DIR.exists() or not USER_ICONS_DIR.exists():
        return deduped

    for target in DATA_DIR.glob("Bibata-Material-*"):
        source = USER_ICONS_DIR / target.name
        if not source.is_dir() or source.is_symlink():
            continue
        if not target.is_dir() or target.is_symlink():
            continue
        if _dir_signature(source) != _dir_signature(target):
            continue
        shutil.rmtree(target)
        _replace_with_link(source, target)
        deduped.append(target.name)
    return deduped


def is_theme_installed(theme_name: str) -> bool:
    return any(
        (base / theme_name).exists()
        for base in (USER_ICONS_DIR, DATA_DIR, Path("/usr/share/icons"), Path("/usr/local/share/icons"))
    )


def normalize_theme_manifests() -> int:
    """Make each Hyprcursor manifest name match its directory name.

    The upstream release ships ``name = Bibata-Modern-Classic`` in every theme,
    which can confuse Hyprcursor into falling back to XCursor rasters.
    """
    changed = 0
    for theme_dir in USER_ICONS_DIR.glob("Bibata-Material-*"):
        manifest = theme_dir / "manifest.hl"
        if not manifest.is_file() or theme_dir.is_symlink():
            continue
        try:
            content = manifest.read_text(encoding="utf-8")
            updated = re.sub(
                r"(?m)^\s*name\s*=.*$",
                f"name = {theme_dir.name}",
                content,
                count=1,
            )
            if updated != content:
                atomic_write(manifest, updated)
                changed += 1
        except OSError:
            continue
    return changed


# --------------------------------------------------------------------------- #
# Persistence writers
# --------------------------------------------------------------------------- #

def update_hypr_vars_lua(theme_name: str, size: int) -> None:
    """Update cursorTheme and cursorSize in ~/.config/caelestia/hypr-vars.lua."""
    theme_name = validate_theme_name(theme_name)
    size = validate_size(size)

    try:
        content = HYPR_VARS_FILE.read_text(encoding="utf-8") if HYPR_VARS_FILE.exists() else "return {\n}\n"
        content, theme_hits = re.subn(
            r'cursorTheme\s*=\s*["\'][^"\']*["\']',
            f'cursorTheme = "{theme_name}"',
            content,
            count=1,
        )
        if theme_hits == 0:
            content, _ = re.subn(
                r"(return\s*\{)",
                rf'\1\n  cursorTheme = "{theme_name}",',
                content,
                count=1,
            )
        content, size_hits = re.subn(
            r"cursorSize\s*=\s*\d+",
            f"cursorSize = {size}",
            content,
            count=1,
        )
        if size_hits == 0:
            content, _ = re.subn(
                r"(return\s*\{)",
                rf"\1\n  cursorSize = {size},",
                content,
                count=1,
            )
        atomic_write(HYPR_VARS_FILE, content)
    except (OSError, ValueError) as exc:
        print(f"[caelestia-material-cursor] Failed to update hypr-vars.lua: {exc}", file=sys.stderr)


def ensure_hypr_env_lua() -> None:
    """Ensure HYPRCURSOR_THEME/SIZE are exported in Hyprland's env.lua."""
    if not HYPR_ENV_FILE.exists():
        return
    try:
        content = HYPR_ENV_FILE.read_text(encoding="utf-8")
        additions = []
        if "HYPRCURSOR_THEME" not in content:
            additions.append('hl.env("HYPRCURSOR_THEME", vars.cursorTheme)')
        if "HYPRCURSOR_SIZE" not in content:
            additions.append('hl.env("HYPRCURSOR_SIZE", vars.cursorSize)')
        if not additions:
            return

        block = "\n".join(additions)
        pattern = r'hl\.env\(["\']XCURSOR_THEME["\'],\s*vars\.cursorTheme\)'
        if re.search(pattern, content):
            content = re.sub(pattern, f"{block}\n\\g<0>", content, count=1)
        else:
            content = content.rstrip("\n") + "\n" + block + "\n"
        atomic_write(HYPR_ENV_FILE, content)
    except OSError as exc:
        print(f"[caelestia-material-cursor] Failed to update env.lua: {exc}", file=sys.stderr)


def update_default_cursor(theme_name: str) -> None:
    """Update ~/.icons/default and the XDG data default for legacy X11/GTK apps."""
    xcursor_content = (
        "[Icon Theme]\n"
        "Name=Default\n"
        "Comment=Default Cursor Theme\n"
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
            atomic_write(default_dir / "index.theme", xcursor_content)
            atomic_write(default_dir / "manifest.hl", hypr_manifest_content)

            source = base_dir / theme_name
            if not source.exists():
                continue
            for sub in ("cursors", "hyprcursors"):
                target_sub = default_dir / sub
                if target_sub.is_symlink() or target_sub.is_file():
                    target_sub.unlink()
                elif target_sub.is_dir():
                    shutil.rmtree(target_sub)
                source_sub = source / sub
                if source_sub.exists():
                    target_sub.symlink_to(f"../{theme_name}/{sub}")
        except OSError:
            continue


# --------------------------------------------------------------------------- #
# Scheme / wallpaper discovery
# --------------------------------------------------------------------------- #

def get_current_wallpaper_path() -> str | None:
    if env_wall := os.environ.get("WALLPAPER_PATH"):
        return env_wall

    if WALLPAPER_CURRENT_SYMLINK.exists():
        try:
            return str(WALLPAPER_CURRENT_SYMLINK.resolve())
        except OSError:
            pass

    if WALLPAPER_PATH_FILE.exists():
        try:
            path_str = WALLPAPER_PATH_FILE.read_text(encoding="utf-8").strip()
            if path_str:
                return path_str
        except OSError:
            pass
    return None


def _path_candidates(path: str | None) -> list[str]:
    if not path:
        return []
    candidates: list[str] = []
    for candidate in (path, os.path.abspath(path)):
        if candidate not in candidates:
            candidates.append(candidate)
    try:
        resolved = str(Path(path).resolve())
        if resolved not in candidates:
            candidates.append(resolved)
    except OSError:
        pass
    return candidates


def _video_thumbnail(path: str) -> Path | None:
    for candidate in _path_candidates(path):
        thumb = CACHE_DIR / "videothumbs" / f"{djb2_hash(candidate)}.jpg"
        if thumb.exists():
            return thumb
    return None


def get_wallpaper_thumbnail(wall_path: str | None = None) -> Path | None:
    if env_thumb := os.environ.get("THUMBNAIL_PATH"):
        thumb = Path(env_thumb)
        if thumb.exists():
            return thumb

    if WALLPAPER_THUMB_FILE.exists():
        return WALLPAPER_THUMB_FILE

    target_str = wall_path or get_current_wallpaper_path()
    if not target_str:
        return None

    target = Path(target_str)
    if target.suffix.lower() in VIDEO_EXTENSIONS:
        return _video_thumbnail(target_str)

    return target if target.exists() else None


def _run_caelestia(args: list[str], timeout: int = 3) -> str | None:
    try:
        result = subprocess.run(
            ["caelestia", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def get_caelestia_scheme() -> dict[str, Any]:
    """Read the active scheme from the hook env, state file, or the CLI."""
    env_mode = os.environ.get("SCHEME_MODE")
    env_name = os.environ.get("SCHEME_NAME")
    if env_mode and env_name:
        colours: dict[str, Any] = {}
        if env_colours := os.environ.get("SCHEME_COLOURS"):
            try:
                parsed = json.loads(env_colours)
                if isinstance(parsed, dict):
                    colours = parsed
            except ValueError:
                pass
        return {"name": env_name, "mode": env_mode, "colours": colours}

    if SCHEME_STATE_FILE.exists():
        try:
            with open(SCHEME_STATE_FILE, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass

    # `caelestia scheme get` is human-readable; ask for the fields individually.
    name = _run_caelestia(["scheme", "get", "-n"])
    mode = _run_caelestia(["scheme", "get", "-m"])
    colours = {}
    raw = _run_caelestia(["scheme", "get"])
    if raw:
        match = re.search(r"primary:\s*(?:\x1b\[[0-9;]*m)?#?([0-9a-fA-F]{6})", raw)
        if match:
            colours["primary"] = match.group(1).lower()
    if name or mode:
        return {"name": name or "dynamic", "mode": (mode or "dark").lower(), "colours": colours}

    return {
        "name": "dynamic",
        "mode": "dark",
        "colours": {"primary": "90caf9", "surface": "121212"},
    }


# --------------------------------------------------------------------------- #
# Cursor application
# --------------------------------------------------------------------------- #

def _gsettings_get(schema: str, key: str) -> str | None:
    try:
        result = subprocess.run(
            ["gsettings", "get", schema, key],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip().strip("'")
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def apply_cursor(theme_name: str, size: int) -> None:
    """Apply the cursor to Hyprland, GTK/GSettings and the persistent configs."""
    theme_name = validate_theme_name(theme_name)
    size = validate_size(size)

    update_hypr_vars_lua(theme_name, size)
    ensure_hypr_env_lua()
    update_default_cursor(theme_name)

    try:
        subprocess.run(
            ["hyprctl", "setcursor", theme_name, str(size)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass

    if shutil.which("gsettings"):
        try:
            if _gsettings_get("org.gnome.desktop.interface", "cursor-theme") != theme_name:
                subprocess.run(
                    ["gsettings", "set", "org.gnome.desktop.interface", "cursor-theme", theme_name],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            if _gsettings_get("org.gnome.desktop.interface", "cursor-size") != str(size):
                subprocess.run(
                    ["gsettings", "set", "org.gnome.desktop.interface", "cursor-size", str(size)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
        except (OSError, subprocess.SubprocessError):
            pass
    elif shutil.which("dconf"):
        try:
            subprocess.run(
                ["dconf", "write", "/org/gnome/desktop/interface/cursor-theme", f"'{theme_name}'"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            subprocess.run(
                ["dconf", "write", "/org/gnome/desktop/interface/cursor-size", str(size)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            pass


# --------------------------------------------------------------------------- #
# XCursor optimizer / restore
# --------------------------------------------------------------------------- #

def filter_xcursor(data: bytes, target_sizes: set[int] | None = None) -> bytes:
    """Keep only image chunks whose nominal size is in *target_sizes*."""
    sizes = target_sizes or {24}
    if data[:4] != b"Xcur":
        return data

    header_len, version, ntoc = struct.unpack("<III", data[4:16])
    matching: list[tuple[int, int, bytes]] = []

    for index in range(ntoc):
        ctype, subtype, pos = struct.unpack("<III", data[16 + index * 12:28 + index * 12])
        if pos + 36 > len(data):
            continue
        chunk = struct.unpack("<IIIIIIIII", data[pos:pos + 36])
        width, height = chunk[4], chunk[5]
        chunk_len = 36 + width * height * 4
        if pos + chunk_len > len(data):
            continue
        if subtype in sizes:
            matching.append((ctype, subtype, data[pos:pos + chunk_len]))

    if not matching:
        return data

    new_ntoc = len(matching)
    new_header = b"Xcur" + struct.pack("<III", header_len, version, new_ntoc)
    toc_offset = 16 + new_ntoc * 12
    toc = bytearray()
    body = bytearray()
    current = toc_offset
    for ctype, subtype, chunk_bytes in matching:
        toc += struct.pack("<III", ctype, subtype, current)
        body += chunk_bytes
        current += len(chunk_bytes)
    return bytes(new_header + toc + body)


def optimize_theme_directory(theme_dir: Path, target_sizes: set[int]) -> tuple[int, int]:
    """Strip unused raster sizes from one theme, writing atomically."""
    cursors_dir = theme_dir / "cursors"
    if not cursors_dir.is_dir():
        return 0, 0

    modified = 0
    saved = 0
    for cursor_file in cursors_dir.iterdir():
        if not cursor_file.is_file() or cursor_file.is_symlink():
            continue
        try:
            original = cursor_file.read_bytes()
            filtered = filter_xcursor(original, target_sizes)
            if len(filtered) >= len(original):
                continue
            tmp = cursor_file.with_name(cursor_file.name + ".tmp")
            try:
                tmp.write_bytes(filtered)
                os.replace(tmp, cursor_file)
            finally:
                tmp.unlink(missing_ok=True)
            saved += len(original) - len(filtered)
            modified += 1
        except OSError:
            continue
    return modified, saved


def _theme_archives() -> list[Path]:
    archives: dict[str, Path] = {}
    for folder in (THEME_CACHE_DIR, SCRIPT_DIR):
        if not folder.exists():
            continue
        for archive in folder.glob("*.tar.gz"):
            archives.setdefault(archive.name, archive)
    return sorted(archives.values())


def source_archives_available() -> bool:
    return bool(_theme_archives())


def optimize_storage(target_sizes: set[int], clean_archives: bool = True) -> dict[str, Any]:
    """Optimize installed themes; move extraction folders to a recoverable trash."""
    sizes = set(target_sizes) or {24}
    total_saved = 0
    files_modified = 0
    themes_optimized = 0
    cleaned: list[str] = []
    skipped: list[str] = []
    archives_available = source_archives_available()

    if clean_archives:
        for folder in USER_ICONS_DIR.glob("bibata-material-*"):
            if not folder.is_dir() or folder.is_symlink():
                continue
            if not archives_available:
                skipped.append(folder.name)
                continue
            try:
                TRASH_DIR.mkdir(parents=True, exist_ok=True)
                destination = TRASH_DIR / f"{folder.name}-{int(time.time())}"
                shutil.move(str(folder), str(destination))
                cleaned.append(folder.name)
            except OSError:
                skipped.append(folder.name)

    seen: set[Path] = set()
    for base_dir in (USER_ICONS_DIR, DATA_DIR):
        if not base_dir.exists():
            continue
        for theme_dir in base_dir.glob("Bibata-Material-*"):
            if not theme_dir.is_dir() or theme_dir.is_symlink():
                continue
            resolved = theme_dir.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            modified, saved = optimize_theme_directory(theme_dir, sizes)
            files_modified += modified
            if saved > 0:
                total_saved += saved
                themes_optimized += 1

    return {
        "target_sizes": sorted(sizes),
        "themes_optimized": themes_optimized,
        "files_modified": files_modified,
        "cleaned_archive_folders": cleaned,
        "skipped_archive_folders": skipped,
        "total_mb_saved": round(total_saved / (1024 * 1024), 2),
    }


def _verify_archive(archive: Path) -> bool:
    """Verify a cached archive against its optional SHA-256 sidecar."""
    sidecar = archive.parent / f"{archive.name}.sha256"
    if not sidecar.exists():
        return True
    try:
        expected = sidecar.read_text(encoding="utf-8").split()[0].lower()
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    except (OSError, ValueError, IndexError):
        return False
    if digest != expected:
        print(f"[caelestia-material-cursor] Checksum mismatch for {archive.name}", file=sys.stderr)
        return False
    return True


def restore_themes(theme: str | None = None) -> list[str]:
    """Restore themes from the cached release archives."""
    archives = _theme_archives()
    if not archives:
        raise FileNotFoundError(
            "No cursor archives found. Put bibata-material-{dark,light}-*.tar.gz in "
            f"{THEME_CACHE_DIR} (or the repository root) and retry."
        )

    USER_ICONS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    THEME_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    restored: list[str] = []

    for archive in archives:
        if not _verify_archive(archive):
            continue
        with tempfile.TemporaryDirectory(dir=THEME_CACHE_DIR, prefix="restore-") as temp_dir:
            try:
                with tarfile.open(archive, "r:gz") as handle:
                    try:
                        handle.extractall(temp_dir, filter="data")
                    except TypeError:  # Python < 3.12
                        handle.extractall(temp_dir)
            except (OSError, tarfile.TarError) as exc:
                print(f"[caelestia-material-cursor] Cannot read {archive}: {exc}", file=sys.stderr)
                continue

            for source in Path(temp_dir).rglob("Bibata-Material-*"):
                if not source.is_dir():
                    continue
                name = source.name
                if theme and theme != "all":
                    wanted = {theme, f"Bibata-Material-{theme}"}
                    if name not in wanted and not name.startswith(f"Bibata-Material-{theme}"):
                        continue

                staging = USER_ICONS_DIR / f".{name}.restore"
                if staging.exists():
                    shutil.rmtree(staging)
                shutil.copytree(source, staging, symlinks=True)

                target = USER_ICONS_DIR / name
                if target.is_symlink() or target.is_file():
                    target.unlink()
                elif target.is_dir():
                    shutil.rmtree(target)
                os.replace(staging, target)
                _replace_with_link(target, DATA_DIR / name)
                restored.append(name)

    normalize_theme_manifests()
    return restored


# --------------------------------------------------------------------------- #
# Wallpaper scanning / cache
# --------------------------------------------------------------------------- #

def _stat_signature(path: str | None) -> tuple[float | None, int | None]:
    if not path:
        return None, None
    try:
        stat = Path(path).stat()
        return stat.st_mtime, stat.st_size
    except OSError:
        return None, None


def _entry_matches(entry: dict[str, Any], path: str) -> bool:
    mtime, size = _stat_signature(path)
    if mtime is None:
        return False
    if entry.get("mtime") is not None and abs(float(entry["mtime"]) - mtime) > 0.001:
        return False
    if entry.get("size") is not None and int(entry["size"]) != size:
        return False
    return True


def get_cache_entry(cache: dict[str, dict[str, Any]], path: str | None) -> dict[str, Any] | None:
    for candidate in _path_candidates(path):
        entry = cache.get(candidate)
        if entry and _entry_matches(entry, candidate):
            return entry
    return None


def _worker_count() -> int:
    cpus = os.cpu_count() or 4
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        ram_gb = (pages * page_size) / (1024 ** 3)
        by_ram = max(2, int(ram_gb / 0.25))
    except (AttributeError, ValueError, OSError):
        by_ram = 8
    return max(2, min(8, cpus, by_ram))


def process_single_wallpaper(
    file_path: Path,
    themes: dict[str, Any],
) -> tuple[str, str, float, int]:
    """Return (path, family, mtime, size) for one static or animated wallpaper."""
    try:
        stat = file_path.stat()
        mtime, size = stat.st_mtime, stat.st_size
    except OSError:
        return str(file_path), "Classic", 0.0, 0

    clean_path = str(file_path)

    if file_path.suffix.lower() in VIDEO_EXTENSIONS:
        cached = _video_thumbnail(clean_path)
        if cached:
            family, _, _ = extract_vibrant_color(cached, themes)
            return clean_path, family, mtime, size

        THUMB_TMP_DIR.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=THUMB_TMP_DIR, prefix="thumb-", suffix=".jpg")
        os.close(fd)
        temp_thumb = Path(temp_name)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", "00:00:01", "-i", clean_path,
                    "-frames:v", "1", "-q:v", "2", str(temp_thumb),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            if temp_thumb.exists() and temp_thumb.stat().st_size > 0:
                family, _, _ = extract_vibrant_color(temp_thumb, themes)
                return clean_path, family, mtime, size
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            temp_thumb.unlink(missing_ok=True)
        return clean_path, "Soft-Blue", mtime, size

    family, _, _ = extract_vibrant_color(file_path, themes)
    return clean_path, family, mtime, size


def scan_all_wallpapers(wallpapers_dir: Path | None = None, prune: bool = False) -> int:
    """Scan all wallpapers, update the cache and optionally prune stale entries."""
    if wallpapers_dir is None:
        wallpapers_dir = Path.home() / "Pictures" / "Wallpapers"

    if not wallpapers_dir.exists():
        print(f"[caelestia-material-cursor] Wallpapers directory not found: {wallpapers_dir}", file=sys.stderr)
        return 0

    ensure_flattened_icons()

    with open(THEMES_FILE, encoding="utf-8") as handle:
        themes = json.load(handle)

    all_files = [
        path for path in wallpapers_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in WALLPAPER_EXTENSIONS
    ]
    if not all_files:
        print("[caelestia-material-cursor] No wallpapers found to scan.")
        return 0

    cache = load_wallpaper_cache()
    pending: list[tuple[str, Path]] = []
    for path in all_files:
        key = str(path)
        entry = cache.get(key)
        if entry and _entry_matches(entry, key):
            continue
        pending.append((key, path))

    added = 0
    for key, family, mtime, size in _map_wallpapers(pending, themes):
        if mtime > 0:
            if key not in cache:
                added += 1
            cache[key] = {"family": family, "mtime": mtime, "size": size}

    removed = 0
    if prune:
        for key in list(cache):
            if not Path(key).exists():
                del cache[key]
                removed += 1

    save_wallpaper_cache(cache)
    print(
        f"[caelestia-material-cursor] Database updated: {added} added, "
        f"{len(pending) - added} updated, {removed} pruned, {len(cache)} total."
    )
    return len(cache)


def _map_wallpapers(
    pending: list[tuple[str, Path]],
    themes: dict[str, Any],
) -> list[tuple[str, str, float, int]]:
    if not pending:
        return []

    def process(item: tuple[str, Path]) -> tuple[str, str, float, int]:
        key, path = item
        _, family, mtime, size = process_single_wallpaper(path, themes)
        return key, family, mtime, size

    results: list[tuple[str, str, float, int]] = []
    with ThreadPoolExecutor(max_workers=_worker_count()) as executor:
        results.extend(executor.map(process, pending))
    return results


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #

def sync(force_recalculate: bool = False, event: str | None = None) -> None:
    """Synchronize the cursor with the active wallpaper and scheme."""
    config = load_config()
    if not config.get("enabled", True):
        return

    if not force_recalculate and check_debounce(event or infer_event()):
        return

    ensure_flattened_icons()

    try:
        cursor_size, size_source = get_active_cursor_size()
    except ValueError as exc:
        print(f"[caelestia-material-cursor] {exc}", file=sys.stderr)
        return

    override = config.get("overrideTheme")
    if override:
        try:
            override = validate_theme_name(override)
        except ValueError as exc:
            print(f"[caelestia-material-cursor] {exc}", file=sys.stderr)
            return
        if not is_theme_installed(override):
            print(
                f"[caelestia-material-cursor] Override theme '{override}' is not installed; keeping current cursor.",
                file=sys.stderr,
            )
            return
        apply_cursor(override, cursor_size)
        state = load_cursor_state()
        state.update({
            "family": state.get("family", "Classic"),
            "mode": state.get("mode", "dark"),
            "wallpaper": get_current_wallpaper_path(),
            "scheme_name": state.get("scheme_name", "dynamic"),
            "applied_theme": override,
            "size": cursor_size,
            "size_source": size_source,
            "override": True,
        })
        save_cursor_state(state)
        return

    if not THEMES_FILE.exists():
        print(f"[caelestia-material-cursor] Error: {THEMES_FILE} not found.", file=sys.stderr)
        return

    with open(THEMES_FILE, encoding="utf-8") as handle:
        themes = json.load(handle)

    caelestia_data = get_caelestia_scheme()
    mode = str(caelestia_data.get("mode", "dark")).lower()
    scheme_name = str(caelestia_data.get("name", "dynamic")).lower()
    colours = caelestia_data.get("colours", {}) or {}

    current_wall = get_current_wallpaper_path()
    last_state = load_cursor_state()
    last_wall = last_state.get("wallpaper")
    last_family = last_state.get("family")
    last_scheme = last_state.get("scheme_name")

    wall_mtime, wall_size = _stat_signature(current_wall)
    same_wall = (
        current_wall == last_wall
        and last_state.get("wallpaper_mtime") == wall_mtime
        and last_state.get("wallpaper_size") == wall_size
    )
    same_scheme = scheme_name == last_scheme

    if scheme_name != "dynamic":
        primary_hex = colours.get("primary") or colours.get("m3primary") or "90caf9"
        if not str(primary_hex).startswith("#"):
            primary_hex = f"#{primary_hex}"
        family, _ = find_closest_theme(primary_hex, themes, kL=2.0)
    elif not force_recalculate and same_wall and same_scheme and last_family:
        family = str(last_family)
    else:
        cache = load_wallpaper_cache()
        entry = get_cache_entry(cache, current_wall)
        if entry and not force_recalculate:
            family = str(entry.get("family", "Soft-Blue"))
        elif current_wall and Path(current_wall).exists():
            # Handles both static images and videos (ffmpeg frame extraction).
            _, family, _, _ = process_single_wallpaper(Path(current_wall), themes)
        else:
            thumb_path = get_wallpaper_thumbnail(current_wall)
            if thumb_path and thumb_path.exists():
                family, _, _ = extract_vibrant_color(thumb_path, themes)
            else:
                primary_hex = colours.get("primary") or colours.get("m3primary") or "90caf9"
                if not str(primary_hex).startswith("#"):
                    primary_hex = f"#{primary_hex}"
                family, _ = find_closest_theme(primary_hex, themes, kL=2.0)

        if current_wall and Path(current_wall).exists():
            cache[current_wall] = {
                "family": family,
                "mtime": wall_mtime,
                "size": wall_size,
            }
            save_wallpaper_cache(cache)

    if family == "Classic":
        target_theme = "Bibata-Material-Classic"
    elif mode == "light":
        target_theme = f"Bibata-Material-{family}-Light"
    else:
        target_theme = f"Bibata-Material-{family}"

    if not is_theme_installed(target_theme):
        if is_theme_installed(f"Bibata-Material-{family}"):
            target_theme = f"Bibata-Material-{family}"
        elif is_theme_installed("Bibata-Material-Classic"):
            target_theme = "Bibata-Material-Classic"
        else:
            print(
                f"[caelestia-material-cursor] No installed theme for '{family}' ({mode}); "
                "keeping current cursor. Run install.sh or --restore.",
                file=sys.stderr,
            )
            return

    apply_cursor(target_theme, cursor_size)

    save_cursor_state({
        "family": family,
        "mode": mode,
        "wallpaper": current_wall,
        "wallpaper_mtime": wall_mtime,
        "wallpaper_size": wall_size,
        "scheme_name": scheme_name,
        "applied_theme": target_theme,
        "size": cursor_size,
        "size_source": size_source,
    })


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #

def build_status() -> dict[str, Any]:
    config = load_config()
    state = load_cursor_state()
    cache = load_wallpaper_cache()
    rec, reason, _ = get_recommended_cursor_size()
    monitor = get_monitor_info()
    size, size_source = get_active_cursor_size()

    unique_themes: dict[Path, Path] = {}
    for base_dir in (USER_ICONS_DIR, DATA_DIR):
        if not base_dir.exists():
            continue
        for theme_dir in base_dir.glob("Bibata-Material-*"):
            if theme_dir.is_dir():
                unique_themes.setdefault(theme_dir.resolve(), theme_dir)

    duplicates = 0
    if DATA_DIR.exists() and USER_ICONS_DIR.exists():
        for theme_dir in DATA_DIR.glob("Bibata-Material-*"):
            if theme_dir.is_dir() and not theme_dir.is_symlink():
                if (USER_ICONS_DIR / theme_dir.name).is_dir():
                    duplicates += 1

    total_bytes = 0
    for theme_dir in unique_themes.values():
        total_bytes += sum(
            item.stat().st_size for item in theme_dir.rglob("*")
            if item.is_file() and not item.is_symlink()
        )

    stale_cache = sum(
        1 for key, entry in cache.items()
        if not Path(key).exists() or not _entry_matches(entry, key)
    )

    return {
        "display": {
            "monitor": monitor["name"],
            "resolution": f"{monitor['width']}x{monitor['height']}",
            "scale": f"{monitor['scale']}x",
            "recommended_cursor_size": f"{rec}px",
            "recommendation_rationale": reason,
        },
        "active_cursor": {
            "theme": state.get("applied_theme", "Bibata-Material-Classic"),
            "family": state.get("family", "Classic"),
            "mode": state.get("mode", "dark"),
            "size": f"{size}px",
            "size_source": size_source,
        },
        "storage": {
            "installed_themes_count": len(unique_themes),
            "duplicate_copies": duplicates,
            "disk_usage_mb": round(total_bytes / (1024 * 1024), 2),
        },
        "config": config,
        "cached_wallpapers_count": len(cache),
        "stale_cache_entries": stale_cache,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cursor_sync.py",
        description="Caelestia Material Cursor Sync CLI",
    )
    parser.add_argument("--scan", action="store_true", help="Rescan wallpapers in ~/Pictures/Wallpapers")
    parser.add_argument("--prune", action="store_true", help="With --scan, drop entries for deleted files")
    parser.add_argument(
        "--optimize",
        nargs="?",
        const="auto",
        metavar="SIZE",
        help="Strip unused cursor sizes (default: auto-detected) and trash duplicate extraction folders",
    )
    parser.add_argument("--force", action="store_true", help="With --optimize, proceed without cached archives")
    parser.add_argument(
        "--restore",
        nargs="?",
        const="all",
        metavar="THEME",
        help="Restore themes from cached release archives (all by default)",
    )
    parser.add_argument("--dedupe", action="store_true", help="Replace duplicate data-dir theme copies with symlinks")
    parser.add_argument(
        "--normalize",
        action="store_true",
        help="Fix Hyprcursor manifest names to match theme directories",
    )
    parser.add_argument("--detect-size", action="store_true", help="Detect display and print cursor size options")
    parser.add_argument("--status", action="store_true", help="Print state, config, cache and storage info")
    parser.add_argument("--toggle", action="store_true", help="Toggle automatic synchronization on/off")
    parser.add_argument("--recalc", action="store_true", help="Force colour extraction, ignoring the cache")
    return parser


def _resolve_optimize_size(raw: str) -> int:
    if raw == "auto":
        return get_recommended_cursor_size()[0]
    return validate_size(raw)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.detect_size:
        rec, reason, options = get_recommended_cursor_size()
        monitor = get_monitor_info()
        print(json.dumps({
            "name": monitor["name"],
            "width": monitor["width"],
            "height": monitor["height"],
            "scale": monitor["scale"],
            "recommended_size": rec,
            "recommended_target_sizes": sorted(get_recommended_target_sizes(rec)),
            "reason": reason,
            "options": options,
        }, indent=2))
        return 0

    if args.status:
        print(json.dumps(build_status(), indent=2))
        return 0

    if args.scan:
        scan_all_wallpapers(prune=args.prune)
        return 0

    if args.optimize is not None:
        try:
            size = _resolve_optimize_size(args.optimize)
        except ValueError as exc:
            print(f"[caelestia-material-cursor] {exc}", file=sys.stderr)
            return 2
        if not source_archives_available() and not args.force:
            print(
                "[caelestia-material-cursor] No cached archives found; refusing to strip cursor sizes "
                f"irreversibly. Put the release .tar.gz files in {THEME_CACHE_DIR} or pass --force.",
                file=sys.stderr,
            )
            return 1
        targets = get_recommended_target_sizes(size)
        print(f"Optimizing theme storage, keeping sizes {sorted(targets)}...")
        result = optimize_storage(targets)
        print(
            f"Done! Optimized {result['themes_optimized']} themes "
            f"({result['files_modified']} files), freed {result['total_mb_saved']} MB."
        )
        if result["cleaned_archive_folders"]:
            print(f"Moved {len(result['cleaned_archive_folders'])} extraction folders to {TRASH_DIR}.")
        if result["skipped_archive_folders"]:
            print(
                f"Kept {len(result['skipped_archive_folders'])} extraction folders (no archives to restore from)."
            )
        return 0

    if args.restore is not None:
        try:
            restored = restore_themes(args.restore)
        except FileNotFoundError as exc:
            print(f"[caelestia-material-cursor] {exc}", file=sys.stderr)
            return 1
        if not restored:
            print("[caelestia-material-cursor] No matching themes found in the archives.", file=sys.stderr)
            return 1
        print(f"Restored {len(restored)} themes: {', '.join(sorted(set(restored)))}")
        return 0

    if args.dedupe:
        deduped = dedupe_icon_dirs()
        print(f"Replaced {len(deduped)} duplicate theme copies with symlinks.")
        return 0

    if args.normalize:
        changed = normalize_theme_manifests()
        print(f"Normalized {changed} Hyprcursor manifests.")
        return 0

    if args.toggle:
        config = load_config()
        config["enabled"] = not config.get("enabled", True)
        save_config(config)
        print(f"Cursor sync enabled: {config['enabled']}")
        if config["enabled"]:
            sync(force_recalculate=True)
        return 0

    if args.recalc:
        sync(force_recalculate=True)
        return 0

    sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
