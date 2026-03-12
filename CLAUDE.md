# xc - two-panel console file manager

## Project overview

Single-file Python two-panel file manager with virtual filesystem support.

- `xc.py` - standalone uv script, all code in one file

## Architecture

- **VFS layer**: Abstract `VFS` base class with implementations: `LocalFS`, `TarFS`, `S3FS`, `GCSFS`, `SSHFS`
- **Panel**: File list panel with VFS stack for nested navigation
- **App**: Top-level curses app managing two panels, menus, commands, keymaps

### VFS pattern

Each VFS implements: `probe()`, `enter()`, `read_dir()`, `read_file()`, `write_file()`, `mkdir_all()`, `leave()`.
Detection is probe-based: iterate `probes` list, first match wins. Add new VFS types to the `probes` list in `App.__init__`.

### Key conventions

- VFS config files: `.s3`, `.gcs`, `.ssh` extensions with key=value content
- Macro expansion: `%f` (filename), `%F` (full path), `%x`/`%X` (without extension), `%m`/`%M` (tagged files), `%d`/`%D` (directory), `%&` (background)
- `%F` on non-local VFS: downloads to temp, runs command, uploads back on success if file changed

## Formatting

Run `black xc.py` after edits.

## Running

```sh
uv run xc.py
```

## State

App state saved to `~/.xc/xc.json` (panel paths, active panel, copy history).
Logs at `~/.xc/xc.log`.
