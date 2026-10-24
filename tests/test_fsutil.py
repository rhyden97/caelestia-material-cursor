import json
from concurrent.futures import ThreadPoolExecutor

from fsutil import atomic_json_dump, atomic_json_load, atomic_write


def test_atomic_write_and_json(tmp_path):
    target = tmp_path / "state.json"
    atomic_json_dump(target, {"enabled": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"enabled": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_json_load_default(tmp_path):
    assert atomic_json_load(tmp_path / "missing.json", default={"x": 1}) == {"x": 1}


def test_concurrent_json_writes_stay_valid(tmp_path):
    target = tmp_path / "state.json"

    def write(index):
        atomic_json_dump(target, {"index": index})

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(50)))

    data = json.loads(target.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert 0 <= data["index"] < 50


def test_atomic_write_replaces_content(tmp_path):
    target = tmp_path / "file.txt"
    atomic_write(target, "first")
    atomic_write(target, "second")
    assert target.read_text(encoding="utf-8") == "second"
