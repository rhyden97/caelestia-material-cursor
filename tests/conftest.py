import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cursor_sync as cs  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolate every path cursor_sync writes to."""
    config = tmp_path / "config"
    state = tmp_path / "state"
    cache = tmp_path / "cache"
    icons = tmp_path / "icons"
    data_icons = tmp_path / "data-icons"
    for folder in (config, state, cache, icons, data_icons):
        folder.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cs, "CONFIG_DIR", config)
    monkeypatch.setattr(cs, "STATE_DIR", state)
    monkeypatch.setattr(cs, "CACHE_DIR", cache)
    monkeypatch.setattr(cs, "USER_ICONS_DIR", icons)
    monkeypatch.setattr(cs, "DATA_DIR", data_icons)
    monkeypatch.setattr(cs, "CONFIG_FILE", config / "cursor.json")
    monkeypatch.setattr(cs, "LEGACY_CONFIG_FILE", config / "cursor_plugin.json")
    monkeypatch.setattr(cs, "CURSOR_STATE_FILE", state / "cursor_state.json")
    monkeypatch.setattr(cs, "WALLPAPER_CACHE_FILE", state / "wallpaper_cursors.json")
    monkeypatch.setattr(cs, "FLATTEN_FLAG_FILE", state / ".icons_flattened")
    monkeypatch.setattr(cs, "SCHEME_STATE_FILE", state / "scheme.json")
    monkeypatch.setattr(cs, "HYPR_VARS_FILE", config / "hypr-vars.lua")
    monkeypatch.setattr(cs, "HYPR_ENV_FILE", config / "env.lua")
    monkeypatch.setattr(cs, "WALLPAPER_PATH_FILE", state / "wallpaper" / "path.txt")
    monkeypatch.setattr(cs, "WALLPAPER_CURRENT_SYMLINK", state / "wallpaper" / "current")
    monkeypatch.setattr(cs, "WALLPAPER_THUMB_FILE", state / "wallpaper" / "thumbnail.jpg")
    monkeypatch.setattr(cs, "THEME_CACHE_DIR", cache / "cursor-themes")
    monkeypatch.setattr(cs, "THUMB_TMP_DIR", cache / "cursor-thumbs")
    monkeypatch.setattr(cs, "TRASH_DIR", cache / "cursor-themes" / "trash")

    monkeypatch.setattr(cs, "ensure_flattened_icons", lambda *args, **kwargs: None)
    monkeypatch.setattr(cs, "check_debounce", lambda *args, **kwargs: False)

    return cs


@pytest.fixture
def themes(env):
    """Install a few fake cursor themes so is_theme_installed() succeeds."""
    for name in (
        "Bibata-Material-Soft-Blue",
        "Bibata-Material-Salmon",
        "Bibata-Material-Teal",
        "Bibata-Material-Classic",
    ):
        (env.USER_ICONS_DIR / name).mkdir(parents=True, exist_ok=True)
    return env
