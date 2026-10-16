# Caelestia Material Cursor Sync (`caelestia-material-cursor`)

Modular CLI plugin and synchronization backend for **Caelestia** (`caelestia-dots`) that dynamically synchronizes cursor theme, size, and accent colors with the active **Material You (M3)** wallpaper palette.

Powered by all color variants from [SakibShahariar/material-bibata-cursor](https://github.com/SakibShahariar/material-bibata-cursor).

---

## Features

- **Sub-5ms Latency (Instant Cached Switching)**: Pre-indexes your wallpaper directory into a local persistent database (`~/.local/state/caelestia/wallpaper_cursors.json`). Changing wallpapers triggers an instantaneous $O(1)$ database lookup. If a new wallpaper is detected, it is extracted on the fly and cached automatically.
- **Native Compositor Synchronization**: Integrates cleanly with Hyprland's runtime environment (`hl.env`), live cursor manager (`hyprctl setcursor`), GTK interface (`gsettings` & `dconf`), and Caelestia dotfiles (`hypr-vars.lua`), ensuring immediate and consistent cursor updates across all applications and the desktop.
- **Intelligent Display Resolution & Scale Detection**: Automatically detects monitor dimensions and scaling factors (e.g., 4K UHD @ 2.0x, unscaled 1440p QHD, standard 1080p FHD), calculating and suggesting the optimal nominal cursor size (24px, 28px, 32px, 48px) to prevent microscopic or blurry cursors on HiDPI displays.
- **Ultra-Lean Resource Footprint (~53 MB instead of ~1.7 GB)**: Features an automatic XCursor binary optimizer that strips unused nominal raster sizes, keeping vector **Hyprcursors** (resolution-independent crisp SVGs) for Wayland plus the optimal **24px** cursor size for legacy X11/GTK apps. Reduces theme storage by over **97%**.
- **Intelligent Vibrant Color Extraction**: Employs an HSV-quantized 24-bin hue clustering algorithm on wallpaper thumbnails. Intelligently scores saturation and chromatic vibrancy $(\text{mean\_s}^{2.2} \times \text{max\_s}^{0.8} \times \text{mean\_v}^{0.5} \times \ln(1 + \text{count}))$ to extract punchy, prominent colors (e.g. glowing lava in a volcano, flowers, neon signs) rather than washed-out dark backgrounds or muted pastels.
- **Perceptual CIEDE2000 Matching**: Uses the international CIELAB CIEDE2000 color difference formula with tuned $k_L = 2.0$ weighting to prioritize hue and chroma accuracy over relative luminance.
- **Color Family Preservation (Dark ↔ Light)**: When switching between Dark and Light mode (or vice-versa), the active color family (e.g., `Teal`, `Soft-Blue`, `Pink-Pastel`) is preserved, cleanly toggling only the `-Light` suffix without jumping to different color tones.
- **Full Animated Wallpaper Support**: Directly supports `.mp4`, `.webm`, and `.mkv` live wallpapers in `~/Pictures/Wallpapers/Animated/`, utilizing Quickshell's `djb2` thumbnail cache and `ffmpeg`.
- **Silent & Non-Intrusive Background Changes**: Wallpaper changes execute silently in the background without notification popups.
- **Dedicated Batch Indexing Notification**: When rescanning or pre-indexing wallpapers, a clean desktop notification is sent using a high-resolution, centered **`Bibata-Material-Noir-Light`** cursor pointer icon.
- 🔄 **Comprehensive System Synchronization**:
  - **Hyprland (Wayland)**: Live compositor reload via `hyprctl setcursor` and runtime `hl.env`.
  - **GTK & Applications**: Real-time GSettings & dconf sync (`org.gnome.desktop.interface cursor-theme`).
  - **Reboot Persistence**: Writes settings directly to `~/.config/caelestia/hypr-vars.lua` (`cursorTheme` and `cursorSize`).
  - **X11 / Legacy Fallback**: Updates `~/.icons/default/index.theme` and `~/.local/share/icons/default/index.theme`.
  - **Dual Caelestia Hooks**: Registered in both `theme.postHook` and `wallpaper.postHook` in `~/.config/caelestia/cli.json`.

---

## Directory Structure & Footprint

The repository is ultra-lean (~390 KB) and does not store heavy compiled binaries in git:

```
caelestia-material-cursor/          # Total repo size: ~390 KB
├── manifest.json                   # Caelestia Plugin manifest (headless hook)
├── themes.json                     # Color definitions for all 57 Bibata themes
├── color_match.py                  # CIEDE2000 Delta-E & vibrant extraction engine
├── cursor_sync.py                  # Core synchronization CLI & database scanner
├── install.sh                      # Automated installer and setup script
├── requirements.txt                # Python dependencies (Pillow)
├── LICENSE                         # MIT License
├── assets/                         # Notification pointer icons
│   ├── cursor_icon.svg             # Clean SVG vector (Noir-Light pointer)
│   └── cursor_icon.png             # 128x128 High-DPI rendered PNG
└── README.md                       # Documentation
```

- **Repository Footprint**: ~390 KB (pure source code, no bloated blobs in Git)
- **Pre-computed Database**: ~83 KB (`~/.local/state/caelestia/wallpaper_cursors.json`)
- **Installed Cursor Themes**: **~53 MB total** across all 57 themes in `~/.icons/` (optimized on installation)

---

## Prerequisites & Dependencies

### System Packages
Ensure Python 3, Hyprland, and standard utilities are installed:

- **Arch Linux / CachyOS**:
  ```bash
  sudo pacman -S python python-pillow hyprland curl tar
  # Optional (recommended for animated wallpapers & vector icon rendering):
  sudo pacman -S ffmpeg librsvg
  ```
- **Fedora**:
  ```bash
  sudo dnf install python3 python3-pillow hyprland curl tar ffmpeg librsvg2-tools
  ```
- **Debian / Ubuntu**:
  ```bash
  sudo apt install python3 python3-pil hyprland curl tar ffmpeg librsvg2-bin
  ```

### Python Dependencies
The color engine uses pure-Python CIEDE2000 algorithms and only requires `Pillow`:

```bash
pip install -r requirements.txt
```

---

## Quick Installation

Run the automated installer from the repository root:

```bash
chmod +x install.sh
./install.sh
```

*(You can also pass `./install.sh -y` to auto-accept the recommended cursor size).*

### What `install.sh` does:
1. **Validates Dependencies**: Verifies Python 3, Pillow, curl/wget, and tar before proceeding.
2. **Detects Display & Optimal Size**: Queries `cursor_sync.py --detect-size` and presents an intelligent size recommendation based on your display resolution and scaling factor.
3. **Dynamic Cursor Theme Download**: Automatically downloads the official [SakibShahariar/material-bibata-cursor v1.3.0](https://github.com/SakibShahariar/material-bibata-cursor/releases) packages on-demand, or uses local archives if placed in the folder.
4. **Optimizes Theme Storage**: Trims unused nominal sizes to preserve disk space (~53 MB instead of 1.7 GB).
5. **Configures Notification Icon**: Sets up the centered `Bibata-Material-Noir-Light` icon.
6. **Registers Caelestia postHooks**: Connects `cursor_sync.py` to `theme.postHook` and `wallpaper.postHook` in `~/.config/caelestia/cli.json`.
7. **Links Plugin**: Symlinks to `~/.local/share/caelestia/plugins/caelestia-material-cursor`.
8. **Pre-indexes Database & Syncs**: Scans wallpapers in parallel and synchronizes the cursor immediately.

---

## CLI Commands Reference

All management is handled via the command line:

```bash
# Perform an immediate cursor sync with current active wallpaper & scheme
python3 cursor_sync.py

# Rescan and pre-index all wallpapers in ~/Pictures/Wallpapers in parallel
python3 cursor_sync.py --scan

# Detect monitor resolution & scale, and display recommended cursor sizes
python3 cursor_sync.py --detect-size

# Optimize cursor theme storage (strips unused sizes, auto-detects optimal size if omitted)
python3 cursor_sync.py --optimize [SIZE]

# View comprehensive status (display info, active cursor, disk usage, and cache count)
python3 cursor_sync.py --status

# Toggle automatic synchronization on / off
python3 cursor_sync.py --toggle

# Force color re-extraction ignoring the database cache
python3 cursor_sync.py --recalc

# Show available CLI options
python3 cursor_sync.py --help
```

---

## Configuration (`~/.config/caelestia/cursor_plugin.json`)

```json
{
  "enabled": true,
  "size": 24,
  "notify": true,
  "overrideTheme": null
}
```

- `enabled` (*bool*): Enables or pauses automatic synchronization.
- `size` (*int*): Cursor size in pixels (`24`, `28`, `32`, `36`, `48`).
- `notify` (*bool*): Enables desktop notifications on batch `--scan` operations.
- `overrideTheme` (*string | null*): Manually lock to a specific theme (e.g. `"Bibata-Material-Sky-Blue"`). Set to `null` for dynamic matching.

---

## License

Distributed under the [MIT License](LICENSE).  
Themes based on [SakibShahariar/material-bibata-cursor](https://github.com/SakibShahariar/material-bibata-cursor) (GPL-3.0).
