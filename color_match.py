#!/usr/bin/env python3
"""color_match.py — CIEDE2000 matcher and vibrant wallpaper colour extractor.

Calculates perceptually uniform CIEDE2000 Delta-E in CIELAB colour space with
kL weighting focused on hue/chroma fidelity, and extracts prominent colours
from wallpapers/thumbnails using Pillow (imported lazily).
"""

from __future__ import annotations

import colorsys
import math
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

# Guard against decompression bombs: 50 MP is far above any wallpaper we accept.
_MAX_IMAGE_PIXELS = 50_000_000
_Image: Any = None


def _pil() -> Any:
    """Import Pillow lazily so CLI startup does not pay for it."""
    global _Image
    if _Image is None:
        from PIL import Image as PILImage

        PILImage.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
        _Image = PILImage
    return _Image


def djb2_hash(value: str) -> str:
    """Match Quickshell's djb2 hash (UTF-16 code units, like JS charCodeAt)."""
    h = 5381
    for char in value:
        code = ord(char)
        if code > 0xFFFF:
            code -= 0x10000
            units = (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF))
        else:
            units = (code,)
        for unit in units:
            h = ((h << 5) + h) + unit
            h &= 0xFFFFFFFF
    return str(h)


def hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"Invalid hex color: {hex_color!r}")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return r / 255.0, g / 255.0, b / 255.0


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_xyz(r: float, g: float, b: float) -> tuple[float, float, float]:
    r, g, b = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)
    # sRGB D65 matrix
    x = r * 0.4124564 + g * 0.3575761 + b * 0.1804375
    y = r * 0.2126729 + g * 0.7151522 + b * 0.0721750
    z = r * 0.0193339 + g * 0.1191920 + b * 0.9503041
    return x * 100.0, y * 100.0, z * 100.0


# D65 reference white
_XN, _YN, _ZN = 95.0489, 100.0, 108.8840


def _f(t: float) -> float:
    delta = 6.0 / 29.0
    if t > delta ** 3:
        return t ** (1.0 / 3.0)
    return t / (3 * delta ** 2) + 4.0 / 29.0


def xyz_to_lab(x: float, y: float, z: float) -> tuple[float, float, float]:
    fx, fy, fz = _f(x / _XN), _f(y / _YN), _f(z / _ZN)
    lightness = 116 * fy - 16
    a = 500 * (fx - fy)
    b = 200 * (fy - fz)
    return lightness, a, b


@lru_cache(maxsize=256)
def hex_to_lab(hex_color: str) -> tuple[float, float, float]:
    return xyz_to_lab(*rgb_to_xyz(*hex_to_rgb(hex_color)))


def ciede2000(
    lab1: tuple[float, float, float],
    lab2: tuple[float, float, float],
    kL: float = 2.0,
    kC: float = 1.0,
    kH: float = 1.0,
) -> float:
    """CIEDE2000 colour difference (default kL=2.0 relaxes lightness sensitivity)."""
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2

    c1 = math.hypot(a1, b1)
    c2 = math.hypot(a2, b2)
    c_bar = (c1 + c2) / 2.0

    g = 0.5 * (1.0 - math.sqrt((c_bar ** 7) / (c_bar ** 7 + 25.0 ** 7)))
    a1_p = (1.0 + g) * a1
    a2_p = (1.0 + g) * a2

    c1_p = math.hypot(a1_p, b1)
    c2_p = math.hypot(a2_p, b2)

    def _hue(ap: float, b: float) -> float:
        if ap == 0 and b == 0:
            return 0.0
        val = math.degrees(math.atan2(b, ap))
        return val if val >= 0 else val + 360.0

    h1_p = _hue(a1_p, b1)
    h2_p = _hue(a2_p, b2)

    delta_l_p = l2 - l1
    delta_c_p = c2_p - c1_p

    if c1_p * c2_p == 0:
        delta_h_p = 0.0
    elif abs(h1_p - h2_p) <= 180.0:
        delta_h_p = h2_p - h1_p
    elif h2_p <= h1_p:
        delta_h_p = (h2_p - h1_p) + 360.0
    else:
        delta_h_p = (h2_p - h1_p) - 360.0

    delta_h = 2.0 * math.sqrt(c1_p * c2_p) * math.sin(math.radians(delta_h_p / 2.0))

    l_bar_p = (l1 + l2) / 2.0
    c_bar_p = (c1_p + c2_p) / 2.0

    if c1_p * c2_p == 0:
        h_bar_p = h1_p + h2_p
    elif abs(h1_p - h2_p) <= 180.0:
        h_bar_p = (h1_p + h2_p) / 2.0
    elif (h1_p + h2_p) < 360.0:
        h_bar_p = (h1_p + h2_p + 360.0) / 2.0
    else:
        h_bar_p = (h1_p + h2_p - 360.0) / 2.0

    t = (
        1.0
        - 0.17 * math.cos(math.radians(h_bar_p - 30.0))
        + 0.24 * math.cos(math.radians(2.0 * h_bar_p))
        + 0.32 * math.cos(math.radians(3.0 * h_bar_p + 6.0))
        - 0.20 * math.cos(math.radians(4.0 * h_bar_p - 63.0))
    )

    delta_theta = 30.0 * math.exp(-(((h_bar_p - 275.0) / 25.0) ** 2))
    r_c = 2.0 * math.sqrt((c_bar_p ** 7) / (c_bar_p ** 7 + 25.0 ** 7))
    s_l = 1.0 + ((0.015 * ((l_bar_p - 50.0) ** 2)) / math.sqrt(20.0 + ((l_bar_p - 50.0) ** 2)))
    s_c = 1.0 + 0.045 * c_bar_p
    s_h = 1.0 + 0.015 * c_bar_p * t
    r_t = -math.sin(math.radians(2.0 * delta_theta)) * r_c

    return math.sqrt(
        (delta_l_p / (kL * s_l)) ** 2
        + (delta_c_p / (kC * s_c)) ** 2
        + (delta_h / (kH * s_h)) ** 2
        + r_t * (delta_c_p / (kC * s_c)) * (delta_h / (kH * s_h))
    )


def find_closest_theme(
    target_hex: str,
    themes: dict[str, dict[str, Any]],
    kL: float = 2.0,
) -> tuple[str, float]:
    """Return the theme whose primary colour is perceptually closest to *target_hex*."""
    target_lab = hex_to_lab(target_hex)
    best_name = "Classic"
    best_dist = float("inf")

    for name, data in themes.items():
        if name == "Classic":
            continue
        primary_hex = data.get("primary", "")
        if not primary_hex:
            continue
        try:
            dist = ciede2000(target_lab, hex_to_lab(primary_hex), kL=kL)
        except (ValueError, ZeroDivisionError):
            continue
        if dist < best_dist:
            best_dist = dist
            best_name = name

    return best_name, best_dist


def extract_vibrant_color(
    image_path: Path | str,
    themes: dict[str, dict[str, Any]],
) -> tuple[str, str, float]:
    """Extract the most representative vibrant colour from an image.

    Returns ``(theme_name, hex_color, delta_e)``; grayscale images map to Classic.
    """
    path = Path(image_path)
    if not path.exists():
        return "Classic", "#000000", 0.0

    image_module = _pil()
    if image_module is None:
        return "Classic", "#000000", 0.0

    try:
        with image_module.open(path) as img:
            # Fast JPEG draft, then decode a small RGB buffer.
            if path.suffix.lower() in {".jpg", ".jpeg"}:
                try:
                    img.draft("RGB", (128, 128))
                except Exception:
                    pass

            img = img.convert("RGB")
            if max(img.size) > 256:
                factor = max(1, max(img.size) // 256)
                try:
                    img = img.reduce(factor)
                except (ValueError, OSError):
                    pass
            img.thumbnail((64, 64), image_module.Resampling.BOX)

            num_bins = 24
            bins = [
                {
                    "s_sum": 0.0,
                    "v_sum": 0.0,
                    "r_sum": 0.0,
                    "g_sum": 0.0,
                    "b_sum": 0.0,
                    "count": 0,
                    "s_max": 0.0,
                }
                for _ in range(num_bins)
            ]

            total_chromatic = 0
            raw_bytes = img.tobytes()

            for i in range(0, len(raw_bytes), 3):
                r, g, b = raw_bytes[i], raw_bytes[i + 1], raw_bytes[i + 2]
                h_val, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)

                # Ignore blacks, highlights and gray/monochrome pixels.
                if v < 0.18 or v > 0.98 or s < 0.18:
                    continue

                bin_idx = int((h_val * 360.0) / (360.0 / num_bins)) % num_bins
                info = bins[bin_idx]
                info["s_sum"] += s
                info["v_sum"] += v
                info["r_sum"] += r
                info["g_sum"] += g
                info["b_sum"] += b
                info["count"] += 1
                if s > info["s_max"]:
                    info["s_max"] = s
                total_chromatic += 1

            if total_chromatic < 25:
                return "Classic", "#000000", 0.0

            scored = []
            for info in bins:
                if info["count"] < 6:
                    continue
                mean_s = info["s_sum"] / info["count"]
                mean_v = info["v_sum"] / info["count"]
                score = (
                    (mean_s ** 2.2)
                    * (info["s_max"] ** 0.8)
                    * (mean_v ** 0.5)
                    * math.log(1 + info["count"])
                )
                avg_r = int(info["r_sum"] / info["count"])
                avg_g = int(info["g_sum"] / info["count"])
                avg_b = int(info["b_sum"] / info["count"])
                scored.append((score, avg_r, avg_g, avg_b))

            if not scored:
                return "Classic", "#000000", 0.0

            scored.sort(key=lambda item: item[0], reverse=True)
            _, red, green, blue = scored[0]
            hex_color = f"#{red:02x}{green:02x}{blue:02x}"
            best_theme, dist = find_closest_theme(hex_color, themes, kL=2.0)
            return best_theme, hex_color, dist

    except Exception as exc:  # Pillow raises several unrelated decode errors
        if "DecompressionBomb" in type(exc).__name__:
            print(f"[caelestia-material-cursor] Refusing oversized image: {path}", file=sys.stderr)
        return "Classic", "#000000", 0.0
