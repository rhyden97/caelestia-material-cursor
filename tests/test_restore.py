import tarfile


def test_restore_from_cached_archive(env, tmp_path):
    env.THEME_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    archive = env.THEME_CACHE_DIR / "bibata-material-dark-v1.3.0.tar.gz"

    source = tmp_path / "pack" / "Bibata-Material-Salmon"
    (source / "cursors").mkdir(parents=True)
    (source / "cursors" / "left_ptr").write_bytes(b"Xcur")
    (source / "manifest.hl").write_text("name = Salmon\n", encoding="utf-8")

    with tarfile.open(archive, "w:gz") as handle:
        handle.add(source, arcname="bibata-material-dark/Bibata-Material-Salmon")

    restored = env.restore_themes()

    assert "Bibata-Material-Salmon" in restored
    target = env.USER_ICONS_DIR / "Bibata-Material-Salmon"
    assert (target / "cursors" / "left_ptr").exists()
    assert (env.DATA_DIR / "Bibata-Material-Salmon").is_symlink()


def test_restore_rejects_bad_checksum(env, tmp_path):
    env.THEME_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    archive = env.THEME_CACHE_DIR / "bibata-material-dark-v1.3.0.tar.gz"

    source = tmp_path / "pack" / "Bibata-Material-Salmon"
    (source / "cursors").mkdir(parents=True)
    (source / "cursors" / "left_ptr").write_bytes(b"Xcur")

    with tarfile.open(archive, "w:gz") as handle:
        handle.add(source, arcname="bibata-material-dark/Bibata-Material-Salmon")

    (env.THEME_CACHE_DIR / f"{archive.name}.sha256").write_text("0" * 64, encoding="utf-8")

    assert env.restore_themes() == []
    assert not (env.USER_ICONS_DIR / "Bibata-Material-Salmon").exists()


def test_restore_without_archives_raises(env):
    try:
        env.restore_themes()
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("restore_themes should raise without archives")


def test_normalize_manifest_names(env):
    theme = env.USER_ICONS_DIR / "Bibata-Material-Test"
    theme.mkdir()
    manifest = theme / "manifest.hl"
    manifest.write_text(
        "name = Bibata-Modern-Classic\nversion = 0.1\ncursors_directory = hyprcursors\n",
        encoding="utf-8",
    )

    assert env.normalize_theme_manifests() == 1
    content = manifest.read_text(encoding="utf-8")
    assert "name = Bibata-Material-Test" in content
    assert "Bibata-Modern-Classic" not in content
