import pytest

import cursor_sync as cs


@pytest.mark.parametrize("name", ["Bibata-Material-Soft-Blue", "Bibata-Material-Sky-Blue-Light", "A"])
def test_valid_theme_names(name):
    assert cs.validate_theme_name(name) == name


@pytest.mark.parametrize(
    "name",
    ["", "../escape", "a b", "a;b", "a'b", "a\"b", "-leading", "a/b", None, 42],
)
def test_invalid_theme_names(name):
    with pytest.raises(ValueError):
        cs.validate_theme_name(name)


@pytest.mark.parametrize("size", [16, 24, 32, 48, 96, "32"])
def test_valid_sizes(size):
    assert cs.validate_size(size) == int(size)


@pytest.mark.parametrize("size", [0, 15, 97, 1000, "large", None])
def test_invalid_sizes(size):
    with pytest.raises(ValueError):
        cs.validate_size(size)


def test_size_precedence(env):
    env.save_config({"enabled": True, "size": 32, "overrideTheme": None})
    assert env.get_active_cursor_size() == (32, "config")

    env.save_config({"enabled": True, "size": None, "overrideTheme": None})
    env.HYPR_VARS_FILE.write_text("return {\n  cursorSize = 28,\n}\n", encoding="utf-8")
    assert env.get_active_cursor_size() == (28, "hypr-vars")


def test_config_drops_removed_keys(env):
    env.LEGACY_CONFIG_FILE.write_text(
        '{"enabled": true, "size": 24, "legacySetting": true}',
        encoding="utf-8",
    )
    config = env.load_config()
    assert "legacySetting" not in config
    env.save_config(config)
    assert "legacySetting" not in env.CONFIG_FILE.read_text(encoding="utf-8")
