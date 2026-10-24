# Changelog

## 1.1.0 — 2026-10-24

Audit remediation release.

### Fixed

- **Irreversible cursor stripping**: `--optimize` now writes XCursor files atomically, keeps
  release archives in `~/.cache/caelestia/cursor-themes/`, moves extraction folders to a
  recoverable trash, and refuses to run without archives unless `--force` is used.
- **Added `--restore`** to rebuild themes from the cached archives.
- **Archive integrity**: cached `.tar.gz` files are verified against optional SHA-256 sidecars
  before being restored.
- **Symlink attack surface**: video thumbnails now use `XDG_CACHE_HOME` with `mkstemp`
  instead of a predictable `/tmp` filename.
- **Lua/shell injection**: theme names and sizes are validated; the interpolated
  `hyprctl eval` block was removed.
- **Non-atomic writes**: every JSON/Lua/config write goes through `fsutil.atomic_write`.
- **Configured size ignored**: precedence is now config > `hypr-vars.lua` > auto-detect,
  and `--status` reports the source.
- **Stale colour family**: cache entries are keyed by `mtime` and size; switching between
  static and dynamic schemes recomputes instead of reusing the previous family.
- **Missing themes**: the sync aborts without changing the cursor when the target theme is
  not installed.
- **Cache growth**: `--scan --prune` drops entries for deleted wallpapers.
- **Duplicate themes**: the installer links themes into `~/.local/share/icons` instead of
  copying them; `--dedupe` fixes existing duplicate copies.
- **Scheme fallback**: uses `caelestia scheme get -n/-m` and parses the primary colour,
  instead of expecting JSON from a human-readable command.
- **HiDPI**: optimization keeps the nominal size and its physical twin (for example 24 and 48).
- **Hyprcursor manifests**: the upstream `name = Bibata-Modern-Classic` field is corrected to
  match each theme directory (`--normalize`).

### Changed

- Pillow is imported lazily; startup no longer pays for it.
- `gsettings`/`dconf` writes only happen when the value actually changes.
- The configuration file is now `~/.config/caelestia/cursor.json`
  (the legacy `cursor_plugin.json` is still read).
- The integration is documented as a CLI hook; `install.sh` no longer creates the inert
  integration symlink.
- `requirements.txt` now requires `Pillow >= 9.1`.

### Removed

- Desktop notifications (`notify-send`), the `notify` setting and the notification icon assets.
- The unused `hyprctl eval` Lua synchronization block.

### Added

- `fsutil.py` atomic helpers.
- `tests/` pytest suite, `pyproject.toml` (ruff/pytest) and GitHub Actions CI.
- `uninstall.sh` with `--purge` and `--themes` options.
