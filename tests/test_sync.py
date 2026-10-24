import os
import time


def _prepare(env, monkeypatch, wall, families):
    calls = []
    monkeypatch.setattr(env, "get_current_wallpaper_path", lambda: str(wall))
    monkeypatch.setattr(env, "get_wallpaper_thumbnail", lambda *args, **kwargs: wall)
    monkeypatch.setattr(env, "apply_cursor", lambda theme, size: calls.append((theme, size)))
    monkeypatch.setattr(
        env,
        "extract_vibrant_color",
        lambda path, themes: (families.pop(0), "#000000", 1.0),
    )
    env.save_config({"enabled": True, "size": 24, "overrideTheme": None})
    return calls


def test_static_to_dynamic_recomputes_family(env, themes, monkeypatch, tmp_path):
    wall = tmp_path / "wall.png"
    wall.write_bytes(b"wallpaper")
    calls = _prepare(env, monkeypatch, wall, ["Salmon", "Teal"])

    monkeypatch.setattr(
        env,
        "get_caelestia_scheme",
        lambda: {"name": "dynamic", "mode": "dark", "colours": {}},
    )
    env.sync()
    assert calls[-1][0] == "Bibata-Material-Salmon"

    # A static scheme now drives the colour.
    monkeypatch.setattr(
        env,
        "get_caelestia_scheme",
        lambda: {"name": "catppuccin", "mode": "dark", "colours": {"primary": "#b2c5ff"}},
    )
    env.sync()
    assert calls[-1][0] == "Bibata-Material-Soft-Blue"

    # Back to dynamic with the same wallpaper must use the wallpaper cache again,
    # not keep the static scheme's family.
    monkeypatch.setattr(
        env,
        "get_caelestia_scheme",
        lambda: {"name": "dynamic", "mode": "dark", "colours": {}},
    )
    env.sync()
    assert calls[-1][0] == "Bibata-Material-Salmon"

    # A forced recalculation bypasses the cache.
    env.sync(force_recalculate=True)
    assert calls[-1][0] == "Bibata-Material-Teal"


def test_wallpaper_replaced_in_place_invalidates_cache(env, themes, monkeypatch, tmp_path):
    wall = tmp_path / "wall.png"
    wall.write_bytes(b"first")
    calls = _prepare(env, monkeypatch, wall, ["Salmon", "Teal"])
    monkeypatch.setattr(
        env,
        "get_caelestia_scheme",
        lambda: {"name": "dynamic", "mode": "dark", "colours": {}},
    )

    env.sync()
    assert calls[-1][0] == "Bibata-Material-Salmon"

    wall.write_bytes(b"second-and-longer")
    future = time.time() + 10
    os.utime(wall, (future, future))

    env.sync()
    assert calls[-1][0] == "Bibata-Material-Teal"


def test_light_mode_switches_suffix(env, themes, monkeypatch, tmp_path):
    wall = tmp_path / "wall.png"
    wall.write_bytes(b"wallpaper")
    calls = _prepare(env, monkeypatch, wall, ["Soft-Blue"])
    monkeypatch.setattr(
        env,
        "get_caelestia_scheme",
        lambda: {"name": "dynamic", "mode": "light", "colours": {}},
    )
    (env.USER_ICONS_DIR / "Bibata-Material-Soft-Blue-Light").mkdir()

    env.sync()
    assert calls[-1][0] == "Bibata-Material-Soft-Blue-Light"


def test_missing_override_theme_does_not_apply(env, themes, monkeypatch):
    calls = []
    monkeypatch.setattr(env, "apply_cursor", lambda theme, size: calls.append(theme))
    env.save_config({"enabled": True, "size": 24, "overrideTheme": "Bibata-Material-Does-Not-Exist"})

    env.sync()

    assert calls == []


def test_missing_theme_aborts_without_applying(env, monkeypatch, tmp_path):
    wall = tmp_path / "wall.png"
    wall.write_bytes(b"wallpaper")
    calls = _prepare(env, monkeypatch, wall, ["Salmon"])
    monkeypatch.setattr(
        env,
        "get_caelestia_scheme",
        lambda: {"name": "dynamic", "mode": "dark", "colours": {}},
    )
    # No themes are installed in this test.

    env.sync()

    assert calls == []
