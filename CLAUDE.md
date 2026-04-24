# xc - two-panel console file manager

## Project overview

Single-file Python two-panel file manager with virtual filesystem support.

- `xc.py` - standalone uv script, all code in one file

## Architecture

- **VFS layer**: Abstract `VFS` base class with implementations: `LocalFS`, `TarFS`, `S3FS`, `GCSFS`, `OCIFS`, `GDriveFS`, `SSHFS`, `GrepFS`
- **Panel**: File list panel with VFS stack for nested navigation
- **App**: Top-level curses app managing two panels, menus, commands, keymaps

### VFS pattern

Each VFS implements: `probe()`, `enter()`, `read_dir()`, `read_file()`, `write_file()`, `mkdir_all()`, `leave()`.
Detection is probe-based: iterate `probes` list, first match wins. Add new VFS types to the `probes` list in `App.__init__`.

### Key conventions

- VFS config files: `.s3`, `.gcs`, `.oci`, `.gdrive`, `.ssh` extensions with key=value content
- Macro expansion: `%f` (filename), `%F` (full path), `%x`/`%X` (without extension), `%m`/`%M` (tagged files), `%d`/`%D` (directory), `%&` (background)
- `%F` on non-local VFS: downloads to temp, runs command, uploads back on success if file changed

### OCIFS

- OCI Object Storage VFS, config extension `.oci`
- Config keys: `type=oci`, `bucket`, `OCI_BUCKET_NAMESPACE`, `OCI_USER`, `OCI_FINGERPRINT`, `OCI_TENANCY`, `OCI_REGION`, `OCI_KEY_BASE64` (inline base64 PEM) or `OCI_KEY_FILE` (path to PEM)
- Falls back to `oci.config.from_file()` if no key provided
- Uses `oci` SDK, pagination via `next_start_with`

### GDriveFS

- Google Drive VFS, config extension `.gdrive`
- Config keys: `type=gdrive`, `folder` (root folder ID), `key` (service account JSON path)
- Falls back to `google.auth.default()` if no key provided
- Uses `google-api-python-client` Drive API v3
- Caches folder ID lookups in `_id_cache` (path -> ID mapping)
- All API calls use `supportsAllDrives=True` and `includeItemsFromAllDrives=True` for shared/team drives

### Screen switching

- App uses curses alternate screen; external commands run on the main screen so output stays in scroll buffer
- ESC ESC in panel mode (or Ctrl-O) switches to main screen to review command output
- ESC (or Ctrl-O) in main screen returns to panels
- Bare ESC detection uses `select()` with 50ms timeout to distinguish from escape sequences

### GrepFS

- Flat list VFS showing search results as relative paths in a single level
- `add_path()` appends results incrementally; panel redraws live during search
- ESC during search keeps partial results; ENTER on a result leaves GrepFS and navigates to that file's directory with cursor on the file
- Rendered via `render_grep_result()`: paths truncated from the left with `...` if too wide

### Name truncation

- `shorten_name()` truncates long names as `beginning~.ext` (or `beginning~last3` if no extension)
- Used by `pad_or_truncate()` in panel file rows and by `draw_status_line()` for the file name

### Dimmed files

- `is_dimmed()` and `DIMMED_NAMES` set control which files appear dimmed (`A_DIM`)
- Currently: dot files, `node_modules`, `__pycache__`

### Color pairs

- Color constants: `CP_DEF`, `CP_CURSOR`, `CP_TAGGED`, `CP_BORDER`, `CP_STATUS`, `CP_CMDLINE`, `CP_ERR`, `CP_MENU`, `CP_MENUSEL`, `CP_DIR`, `CP_DIM`
- Initialized in `init_colors()`

### Processes modal (`p`)

- `get_processes()` uses `ps -axwwo pid=,user=,command=` to list processes with the full command line (no width truncation).
- `get_listen_ports()` parses `lsof -Fpn -P -n -iTCP -sTCP:LISTEN` and `-iUDP` output into a `pid -> ["tcp:port", ...]` map.
- `get_process_env(pid, command)`: reads `/proc/<pid>/environ` on Linux; on macOS uses `ps eww -p <pid> -o command=` and strips the known command prefix, parsing the trailing `KEY=VALUE` tokens.
- `filter_processes(procs, query)`: space-separated tokens, all must appear in `command + ports + pid + user` lowercased; tokens prefixed with `-` exclude matches.
- `shorten_middle(s, width)`: replaces the middle of a long string with `...` to fit `width`.
- Modal state on `App`: `proc_mode`, `proc_list`, `proc_filter`, `proc_cursor`, `proc_offset`, `proc_focus` (`filter`/`list`/`env`), `proc_env_cache`, `proc_env_offset`, `proc_kill_confirm`.
- `Tab` cycles `filter` → `list` → `env`; the env section is scrollable with arrow/PgUp/PgDn/Home/End when focused.
- `k` (list focus) or `Ctrl-K` (any focus) triggers a `y/N` kill confirmation drawn on the bottom border; accepted keystroke calls `os.kill(pid, 9)`.

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
