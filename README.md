# Caelestia Material Cursor Sync (`caelestia-material-cursor`)

CLI hook and synchronization backend for **Caelestia** (`caelestia-dots`) that keeps cursor theme, size and accent colour in sync with the active **Material You (M3)** wallpaper palette.

Powered by all colour variants from [SakibShahariar/material-bibata-cursor](https://github.com/SakibShahariar/material-bibata-cursor).

---

## Features

- **Fast cached switching**: pre-indexes your wallpaper directory into a persistent database (`~/.local/state/caelestia/wallpaper_cursors.json`). A wallpaper change is an O(1) lookup; new or modified files are extracted on demand and cached.
- **Correct cache invalidation**: entries store both `mtime` and file size, so replacing a wallpaper in place (same path) is detected.
- **Native compositor sync**: updates Hyprland at runtime (`hyprctl setcursor`), persists `cursorTheme`/`cursorSize` in `~/.config/caelestia/hypr-vars.lua`, and keeps `HYPRCURSOR_*`/`XCURSOR_*` in `~/.config/hypr/hyprland/env.lua`.
- **GTK/X11 fallbacks**: `gsettings` (or `dconf` when GSettings is unavailable), `~/.icons/default/index.theme` and a Hyprcursor `manifest.hl`.
- **Display-aware sizing**: detects monitor resolution and scale, then suggests 24/28/32/48 px. On HiDPI it keeps the nominal size **and** its physical twin when optimizing.
- **Reversible optimization**: strips unused XCursor raster sizes, keeps vector Hyprcursors, and preserves the release archives so `--restore` can bring back every original size.
- **Vibrant colour extraction**: 24-bin HSV hue clustering on a downscaled thumbnail. Scores saturation and vibrancy (`mean_s^2.2 × max_s^0.8 × mean_v^0.5 × ln(1 + count)`) so glowing lava, flowers or neon signs win over muted backgrounds.
- **Perceptual matching**: CIEDE2000 in CIELAB with `kL = 2.0`, prioritising hue and chroma over lightness.
- **Colour-family preservation**: toggling Dark ↔ Light keeps the active family (for example `Teal` or `Soft-Blue`) and only switches the `-Light` suffix.
- **Animated wallpapers**: supports `.mp4`, `.webm` and `.mkv` using Quickshell's `djb2` thumbnail cache (UTF-16 semantics, matching JS `charCodeAt`) and `ffmpeg`.
- **Silent by design**: no desktop popups or sound during wallpaper/theme changes.
- **Atomic state**: every JSON/Lua write uses a same-directory temp file plus `os.replace`, so concurrent hooks cannot truncate config or state.
- **Safe by default**: theme names and sizes are validated before touching Hyprland, GSettings or Lua files.

---

## Directory structure

```
caelestia-material-cursor/
├── cursor_sync.py       # Core sync CLI, cache, optimizer and restore
├── color_match.py       # CIEDE2000 + vibrant colour extraction
├── fsutil.py            # Atomic write / JSON helpers
├── themes.json          # 29 colour families (28 + Classic) used for matching
├── install.sh           # Installer and hook registration
├── uninstall.sh         # Hook removal and optional purge
├── manifest.json        # Experimental integration metadata
├── requirements.txt     # Python dependencies (Pillow)
├── tests/               # pytest suite
├── pyproject.toml       # ruff + pytest configuration
├── LICENSE              # MIT
└── README.md
```

- **Repository footprint**: ~110 KB of source (no compiled cursor blobs).
- **Pre-computed database**: ~83 KB.
- **Installed themes**: 57 directories. The installer keeps one real copy in `~/.icons` and links it from `~/.local/share/icons`. With the default optimization (24 + 48 px retained) the set is ~111 MB logical (~131 MB on disk); keeping a single raster size is ~30 MB. Older installs with two real copies can be fixed with `--dedupe`.

---

## Prerequisites

### System packages

- **Arch Linux / CachyOS**:
  ```bash
  sudo pacman -S python python-pillow hyprland curl tar
  # Optional (animated wallpapers):
  sudo pacman -S ffmpeg
  ```
- **Fedora**:
  ```bash
  sudo dnf install python3 python3-pillow hyprland curl tar ffmpeg
  ```
- **Debian / Ubuntu**:
  ```bash
  sudo apt install python3 python3-pil hyprland curl tar ffmpeg
  ```

### Python dependencies

```bash
pip install --user -r requirements.txt
```

Only `Pillow >= 9.1` is required at runtime, and it is imported lazily (only when a colour actually needs extraction).

---

## Installation

```bash
chmod +x install.sh
./install.sh          # interactive size selection
./install.sh -y       # accept the recommended size
./install.sh --optimize   # also strip unused raster sizes (reversible)
```

### What `install.sh` does

1. Validates Python 3, Pillow, `curl`/`wget` and `tar`.
2. Detects monitor resolution/scale and recommends a cursor size.
3. Downloads the official [SakibShahariar/material-bibata-cursor v1.3.0](https://github.com/SakibShahariar/material-bibata-cursor/releases) archives, or reuses local ones. Archives are kept in `~/.cache/caelestia/cursor-themes/` with a SHA-256 sidecar.
4. Extracts themes into `~/.icons` and links them from `~/.local/share/icons`.
5. Optionally optimizes storage (`--optimize`); by default all raster sizes are preserved.
6. Saves the chosen size to `~/.config/caelestia/cursor.json`.
7. Registers `cursor_sync.py` in both `theme.postHook` and `wallpaper.postHook` of `~/.config/caelestia/cli.json`.
8. Pre-indexes wallpapers and applies the cursor once.

> The integration is a **CLI hook**, not a shell component: Caelestia runs it through the `cli.json` postHooks. `manifest.json` is kept only as experimental metadata for a future loader.

---

## CLI reference

```bash
# Sync now with the active wallpaper and scheme
python3 cursor_sync.py

# Rescan wallpapers (parallel); --prune drops entries for deleted files
python3 cursor_sync.py --scan
python3 cursor_sync.py --scan --prune

# Detect display and print recommended sizes
python3 cursor_sync.py --detect-size

# Strip unused raster sizes (keeps size and its HiDPI twin)
python3 cursor_sync.py --optimize
python3 cursor_sync.py --optimize 32
python3 cursor_sync.py --optimize 32 --force   # without cached archives

# Restore themes from the cached release archives
python3 cursor_sync.py --restore
python3 cursor_sync.py --restore Bibata-Material-Soft-Blue

# Replace duplicate data-dir copies with symlinks
python3 cursor_sync.py --dedupe

# Fix Hyprcursor manifest names to match theme directories
python3 cursor_sync.py --normalize

# Full status (display, active cursor, size source, storage, cache health)
python3 cursor_sync.py --status

# Enable/disable automatic synchronization
python3 cursor_sync.py --toggle

# Force colour re-extraction, ignoring the cache
python3 cursor_sync.py --recalc
```

---

## Configuration (`~/.config/caelestia/cursor.json`)

```json
{
  "enabled": true,
  "size": 24,
  "overrideTheme": null
}
```

- `enabled` (*bool*): pause automatic synchronization.
- `size` (*int*, 16–96): cursor size in pixels.
- `overrideTheme` (*string | null*): lock to a specific installed theme (for example `"Bibata-Material-Sky-Blue"`). Invalid or missing themes are rejected without changing the current cursor.

A legacy config filename is still read as a fallback when `cursor.json` does not exist.

### Size precedence

1. `size` in `cursor.json`.
2. `cursorSize` in `~/.config/caelestia/hypr-vars.lua`.
3. Auto-detected recommendation.

`--status` reports which source is active (`config`, `hypr-vars` or `auto`).

---

## Storage optimization and restore

`--optimize` keeps the nominal size plus its physical HiDPI twin (for example `{24, 48}` on a 2× display), writes each XCursor atomically and keeps the original archives in `~/.cache/caelestia/cursor-themes/`. Duplicate extraction folders are moved to `.../cursor-themes/trash/` instead of being deleted.

If no archive is available, `--optimize` refuses to run unless `--force` is passed. To recover the full set of sizes at any time:

```bash
python3 cursor_sync.py --restore
```

---

## Troubleshooting

- **Cursor does not change**: check `python3 cursor_sync.py --status`, confirm the theme exists in `~/.icons`, and verify the hooks in `~/.config/caelestia/cli.json`.
- **Blurry cursor on HiDPI**: run `python3 cursor_sync.py --optimize` and make sure the size kept matches `cursorSize`; `--status` shows the active size and its source.
- **Wrong colour after changing wallpaper**: run `python3 cursor_sync.py --recalc`; the cache is keyed by path, `mtime` and size.
- **Two copies of every theme**: run `python3 cursor_sync.py --dedupe`.
- **Start from scratch**: `./uninstall.sh --purge` (add `--themes` to remove the cursor themes too).

---

## License

Distributed under the [MIT License](LICENSE).
Themes are based on [SakibShahariar/material-bibata-cursor](https://github.com/SakibShahariar/material-bibata-cursor) (GPL-3.0) and are downloaded at install time; they are not redistributed in this repository.
