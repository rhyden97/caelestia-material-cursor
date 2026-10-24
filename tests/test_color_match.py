import math

from color_match import ciede2000, djb2_hash, find_closest_theme, hex_to_lab


def test_ciede2000_reference_pair():
    # Sharma et al. reference pair: dE00 = 2.0425 for kL = kC = kH = 1.
    lab1 = (50.0, 2.6772, -79.7751)
    lab2 = (50.0, 0.0, -82.7485)
    assert math.isclose(ciede2000(lab1, lab2, kL=1.0, kC=1.0, kH=1.0), 2.0425, abs_tol=0.001)


def test_identical_colours_have_zero_distance():
    lab = hex_to_lab("#b2c5ff")
    assert ciede2000(lab, lab) == 0.0


def test_djb2_ascii_matches_reference():
    assert djb2_hash("a") == "177670"


def test_djb2_uses_utf16_code_units():
    # Quickshell uses JS charCodeAt(), i.e. surrogate pairs for non-BMP chars.
    expected = 5381
    for unit in (0xD83D, 0xDE00):  # U+1F600
        expected = ((expected << 5) + expected) + unit
        expected &= 0xFFFFFFFF
    assert djb2_hash("\U0001F600") == str(expected)


def test_find_closest_theme_self_match():
    themes = {
        "Classic": {"primary": "#ffffff"},
        "Soft-Blue": {"primary": "#b2c5ff"},
        "Teal": {"primary": "#008080"},
    }
    name, distance = find_closest_theme("#b2c5ff", themes)
    assert name == "Soft-Blue"
    assert distance < 0.01
