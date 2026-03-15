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

### Screen switching

- App uses curses alternate screen; external commands run on the main screen so output stays in scroll buffer
- ESC ESC in panel mode (or Ctrl-O) switches to main screen to review command output
- ESC (or Ctrl-O) in main screen returns to panels
- Bare ESC detection uses `select()` with 50ms timeout to distinguish from escape sequences

### Dimmed files

- `is_dimmed()` and `DIMMED_NAMES` set control which files appear dimmed (`A_DIM`)
- Currently: dot files, `node_modules`, `__pycache__`

### Color pairs

- Color constants: `CP_DEF`, `CP_CURSOR`, `CP_TAGGED`, `CP_BORDER`, `CP_STATUS`, `CP_CMDLINE`, `CP_ERR`, `CP_MENU`, `CP_MENUSEL`, `CP_DIR`, `CP_DIM`
- Initialized in `init_colors()`

## Development

- `pyproject.toml` is for local dev tooling only (black settings); `xc.py` is self-contained
- Run `black xc.py` after edits

## Running

```sh
uv run xc.py
```

## State

App state saved to `~/.xc/xc.json` (panel paths, active panel, copy history).
Logs at `~/.xc/xc.log`.
