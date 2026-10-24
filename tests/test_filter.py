import struct

import cursor_sync as cs


def make_xcursor(sizes):
    chunks = []
    for size in sizes:
        header = struct.pack("<IIIIIIIII", 36, 0xFFFD0002, size, 1, size, size, 0, 0, 0)
        chunks.append((0xFFFD0002, size, header + bytes(size * size * 4)))

    ntoc = len(chunks)
    toc = bytearray()
    body = bytearray()
    position = 16 + ntoc * 12
    for ctype, subtype, data in chunks:
        toc += struct.pack("<III", ctype, subtype, position)
        body += data
        position += len(data)
    return b"Xcur" + struct.pack("<III", 16, 0x10000, ntoc) + bytes(toc) + bytes(body)


def parse_xcursor(data):
    assert data[:4] == b"Xcur"
    _, _, ntoc = struct.unpack("<III", data[4:16])
    sizes = []
    for index in range(ntoc):
        ctype, subtype, position = struct.unpack("<III", data[16 + index * 12:28 + index * 12])
        chunk = struct.unpack("<IIIIIIIII", data[position:position + 36])
        length = 36 + chunk[4] * chunk[5] * 4
        assert position + length <= len(data)
        sizes.append((ctype, subtype, length))
    return sizes


def test_filter_keeps_only_requested_size():
    original = make_xcursor([24, 32, 48])
    filtered = cs.filter_xcursor(original, {24})
    assert [item[1] for item in parse_xcursor(filtered)] == [24]
    assert len(filtered) < len(original)


def test_filter_is_safe_without_matching_size():
    original = make_xcursor([24])
    assert cs.filter_xcursor(original, {32}) == original


def test_filter_passthrough_for_non_xcursor():
    assert cs.filter_xcursor(b"PNG-data", {24}) == b"PNG-data"


def test_optimize_theme_directory_is_atomic(tmp_path):
    theme = tmp_path / "Bibata-Material-Test"
    cursors = theme / "cursors"
    cursors.mkdir(parents=True)
    cursor = cursors / "left_ptr"
    cursor.write_bytes(make_xcursor([24, 32, 48]))

    modified, saved = cs.optimize_theme_directory(theme, {24, 48})

    assert modified == 1
    assert saved > 0
    assert [item[1] for item in parse_xcursor(cursor.read_bytes())] == [24, 48]
    assert not list(cursors.glob("*.tmp"))
