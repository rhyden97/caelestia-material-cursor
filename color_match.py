#!/usr/bin/env python3
"""
color_match.py — CIEDE2000-based nearest-theme matcher & vibrant wallpaper color extractor
for Caelestia & Material Bibata Cursors.

Calculates perceptually uniform CIEDE2000 Delta-E difference in CIELAB color space,
with kL weighting focused on hue/chroma fidelity.
Includes intelligent vibrant color extraction from wallpapers/thumbnails with Pillow.
Zero heavy dependencies.
"""

from __future__ import annotations

import colorsys
import math
from functools import lru_cache
from pathlib import Path
from typing import Dict, Tuple, Any

try:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
except ImportError:
    Image = None


def djb2_hash(s: str) -> str:
    """Matches Caelestia Quickshell's djb2 hash for cached video thumbnails."""
    h = 5381
    for char in s:
        h = ((h << 5) + h) + ord(char)
        h &= 0xFFFFFFFF
    return str(h)


def hex_to_rgb(hex_color: str) -> Tuple[float, float, float]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"Invalid hex color: {hex_color!r}")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return r / 255.0, g / 255.0, b / 255.0


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_xyz(r: float, g: float, b: float) -> Tuple[float, float, float]:
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


def xyz_to_lab(x: float, y: float, z: float) -> Tuple[float, float, float]:
    fx, fy, fz = _f(x / _XN), _f(y / _YN), _f(z / _ZN)
    L = 116 * fy - 16
    a = 500 * (fx - fy)
    b = 200 * (fy - fz)
    return L, a, b


@lru_cache(maxsize=256)
def hex_to_lab(hex_color: str) -> Tuple[float, float, float]:
    return xyz_to_lab(*rgb_to_xyz(*hex_to_rgb(hex_color)))


def ciede2000(lab1: Tuple[float, float, float],
              lab2: Tuple[float, float, float],
              kL: float = 2.0, kC: float = 1.0, kH: float = 1.0) -> float:
    """
    CIEDE2000 color difference formula.
    Default kL=2.0 relaxes lightness sensitivity so hue and chroma matching
    dominate, ensuring color tones accurately match the cursor theme palette.
    """
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2

    C1 = math.hypot(a1, b1)
    C2 = math.hypot(a2, b2)
    C_bar = (C1 + C2) / 2.0

    G = 0.5 * (1.0 - math.sqrt((C_bar ** 7) / (C_bar ** 7 + 25.0 ** 7)))
    a1_p = (1.0 + G) * a1
    a2_p = (1.0 + G) * a2

    C1_p = math.hypot(a1_p, b1)
    C2_p = math.hypot(a2_p, b2)

    def _hue(ap: float, b: float) -> float:
        if ap == 0 and b == 0:
            return 0.0
        val = math.degrees(math.atan2(b, ap))
        return val if val >= 0 else val + 360.0

    h1_p = _hue(a1_p, b1)
    h2_p = _hue(a2_p, b2)

    delta_L_p = L2 - L1
    delta_C_p = C2_p - C1_p

    if C1_p * C2_p == 0:
        delta_h_p = 0.0
    elif abs(h1_p - h2_p) <= 180.0:
        delta_h_p = h2_p - h1_p
    elif h2_p <= h1_p:
        delta_h_p = (h2_p - h1_p) + 360.0
    else:
        delta_h_p = (h2_p - h1_p) - 360.0

    delta_H_p = 2.0 * math.sqrt(C1_p * C2_p) * math.sin(math.radians(delta_h_p / 2.0))

    L_bar_p = (L1 + L2) / 2.0
    C_bar_p = (C1_p + C2_p) / 2.0

    if C1_p * C2_p == 0:
        h_bar_p = h1_p + h2_p
    elif abs(h1_p - h2_p) <= 180.0:
        h_bar_p = (h1_p + h2_p) / 2.0
    elif (h1_p + h2_p) < 360.0:
        h_bar_p = (h1_p + h2_p + 360.0) / 2.0
    else:
        h_bar_p = (h1_p + h2_p - 360.0) / 2.0

    T = (1.0
         - 0.17 * math.cos(math.radians(h_bar_p - 30.0))
         + 0.24 * math.cos(math.radians(2.0 * h_bar_p))
         + 0.32 * math.cos(math.radians(3.0 * h_bar_p + 6.0))
         - 0.20 * math.cos(math.radians(4.0 * h_bar_p - 63.0)))

    delta_theta = 30.0 * math.exp(-(((h_bar_p - 275.0) / 25.0) ** 2))
    R_C = 2.0 * math.sqrt((C_bar_p ** 7) / (C_bar_p ** 7 + 25.0 ** 7))
    S_L = 1.0 + ((0.015 * ((L_bar_p - 50.0) ** 2)) / math.sqrt(20.0 + ((L_bar_p - 50.0) ** 2)))
    S_C = 1.0 + 0.045 * C_bar_p
    S_H = 1.0 + 0.015 * C_bar_p * T
    R_T = -math.sin(math.radians(2.0 * delta_theta)) * R_C

    dE = math.sqrt(
        (delta_L_p / (kL * S_L)) ** 2
        + (delta_C_p / (kC * S_C)) ** 2
        + (delta_H_p / (kH * S_H)) ** 2
        + R_T * (delta_C_p / (kC * S_C)) * (delta_H_p / (kH * S_H))
    )
    return dE


def find_closest_theme(target_hex: str,
                       themes: Dict[str, Dict[str, Any]],
                       kL: float = 2.0) -> Tuple[str, float]:
    """
    Finds the theme whose 'primary' color has the lowest CIEDE2000 Delta-E difference
    against `target_hex`. Uses kL=2.0 to balance hue and chroma vs lightness.
    """
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
            cand_lab = hex_to_lab(primary_hex)
            dist = ciede2000(target_lab, cand_lab, kL=kL)
            if dist < best_dist:
                best_dist = dist
                best_name = name
        except Exception:
            continue

    return best_name, best_dist


def extract_vibrant_color(image_path: Path | str,
                           themes: Dict[str, Dict[str, Any]]) -> Tuple[str, str, float]:
    """
    Extracts the most representative and vibrant color from a wallpaper or thumbnail image.
    Groups pixels into 24 hue bins and evaluates saturation, brightness, and prevalence.
    Returns (theme_name, hex_color, delta_e).
    If the image is monochromatic or grayscale, returns ('Classic', '#000000', 0.0).
    """
    p = Path(image_path)
    if not p.exists() or Image is None:
        return "Classic", "#000000", 0.0

    try:
        with Image.open(p) as img:
            # Fast downscale draft for JPEGs
            if p.suffix.lower() in {".jpg", ".jpeg"}:
                try:
                    img.draft("RGB", (128, 128))
                except Exception:
                    pass

            img = img.convert("RGB")
            # Downsample to 64x64 with BOX filter for instant speed (4096 pixels)
            img.thumbnail((64, 64), Image.Resampling.BOX)

            NUM_BINS = 24
            bins = [{
                "s_sum": 0.0,
                "v_sum": 0.0,
                "r_sum": 0.0,
                "g_sum": 0.0,
                "b_sum": 0.0,
                "count": 0,
                "s_max": 0.0
            } for _ in range(NUM_BINS)]

            total_chromatic = 0
            raw_bytes = img.tobytes()

            for i in range(0, len(raw_bytes), 3):
                r, g, b = raw_bytes[i], raw_bytes[i + 1], raw_bytes[i + 2]
                h_val, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)

                # Filter out pure blacks, pure whites/highlights, and gray/monochrome pixels
                if v < 0.18 or v > 0.98 or s < 0.18:
                    continue

                bin_idx = int((h_val * 360.0) / (360.0 / NUM_BINS)) % NUM_BINS
                b_info = bins[bin_idx]
                b_info["s_sum"] += s
                b_info["v_sum"] += v
                b_info["r_sum"] += r
                b_info["g_sum"] += g
                b_info["b_sum"] += b
                b_info["count"] += 1
                if s > b_info["s_max"]:
                    b_info["s_max"] = s
                total_chromatic += 1

            # If there are almost no chromatic pixels, it is a monochromatic/grayscale wallpaper
            if total_chromatic < 25:
                return "Classic", "#000000", 0.0

            scored = []
            for b_info in bins:
                if b_info["count"] < 6:
                    continue
                mean_s = b_info["s_sum"] / b_info["count"]
                mean_v = b_info["v_sum"] / b_info["count"]
                score = (mean_s ** 2.2) * (b_info["s_max"] ** 0.8) * (mean_v ** 0.5) * math.log(1 + b_info["count"])
                avg_r = int(b_info["r_sum"] / b_info["count"])
                avg_g = int(b_info["g_sum"] / b_info["count"])
                avg_b = int(b_info["b_sum"] / b_info["count"])
                scored.append((score, avg_r, avg_g, avg_b, mean_s))

            if not scored:
                return "Classic", "#000000", 0.0

            scored.sort(key=lambda x: x[0], reverse=True)
            best = scored[0]
            hex_color = f"#{best[1]:02x}{best[2]:02x}{best[3]:02x}"

            best_theme, dist = find_closest_theme(hex_color, themes, kL=2.0)
            return best_theme, hex_color, dist

    except Exception:
        return "Classic", "#000000", 0.0
