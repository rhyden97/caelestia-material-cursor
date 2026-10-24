

def test_scan_indexes_and_prunes(env, monkeypatch, tmp_path):
    wall_dir = tmp_path / "walls"
    wall_dir.mkdir()
    first = wall_dir / "a.png"
    second = wall_dir / "b.png"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    monkeypatch.setattr(env, "extract_vibrant_color", lambda path, themes: ("Teal", "#008080", 1.0))

    total = env.scan_all_wallpapers(wall_dir)
    assert total == 2
    cache = env.load_wallpaper_cache()
    assert len(cache) == 2
    assert all("size" in entry for entry in cache.values())

    second.unlink()
    total = env.scan_all_wallpapers(wall_dir, prune=True)
    assert total == 1
    assert len(env.load_wallpaper_cache()) == 1


def test_scan_skips_unchanged_files(env, monkeypatch, tmp_path):
    wall_dir = tmp_path / "walls"
    wall_dir.mkdir()
    (wall_dir / "a.png").write_bytes(b"a")

    calls = []

    def fake_extract(path, themes):
        calls.append(str(path))
        return "Teal", "#008080", 1.0

    monkeypatch.setattr(env, "extract_vibrant_color", fake_extract)

    env.scan_all_wallpapers(wall_dir)
    env.scan_all_wallpapers(wall_dir)

    assert len(calls) == 1
