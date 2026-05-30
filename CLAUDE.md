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
- `Tab` cycles `filter` → `list` → `env`; the env section is scrollable with Up/Down/PgUp/PgDn/Home/End when focused. `Left`/`Right` act as PgUp/PgDn in both the process list and the env scroll.
- `k` (list focus) or `Ctrl-K` (any focus) triggers a `y/N` kill confirmation drawn on the bottom border; accepted keystroke calls `os.kill(pid, 9)`.

### PATH viewer (`o`)

- `list_path_entries()` splits `$PATH` on `os.pathsep`, deduplicates, and counts executable files (`st_mode & 0o111`, non-dir) per directory. Missing directories produce a `PathEntry(exists=False)`.
- `list_executables(path)` returns sorted executable file names in a directory.
- Modal state on `App`: `path_mode`, `path_entries`, `path_cursor`, `path_offset`, plus nested `path_exe_mode`, `path_exe_list`, `path_exe_cursor`, `path_exe_offset`, `path_exe_path`.
- Enter on an existing PATH entry opens a second modal (drawn on top via `draw_path_exe_modal`) with the executables list. Esc dismisses the inner modal first, then the outer one.
- `_draw_box(x0, y0, w, h, title)` is a helper used by both modals for the framed rectangle with centered title.
- `Left`/`Right` act as PgUp/PgDn in both the paths and executables modals.

### Modal input dialog

- All user input that used to prompt inline on the status/command line now goes through a centered modal dialog with a border and a drop shadow (`_draw_shadow`). Replaces the old `prompt_mode`/`copy_mode` inline editing.
- `open_dialog(title, labels, initials, buttons, action, message="", danger=False, hist_key=-1)` builds a dialog with 0, 1, or 2 stacked input fields plus buttons. `action` receives the list of field values on confirm.
- `show_prompt()` is now a thin wrapper that opens a single-field dialog (mkdir/touch/chmod/rename/chdir all use it unchanged).
- Copy/move: single file shows two fields (`from:` / `to:`); tagged shows one (`to:`) with a `Copy 3 tagged files to:` message. `_exec_copy_move()` runs the operation.
- Delete uses a red dialog (`danger=True`, `CP_DLG_RED`) with a confirmation message and `Delete`/`Cancel` buttons — no input field.
- Dialog state on `App`: `dlg_active`, `dlg_title`, `dlg_message`, `dlg_labels`, `dlg_fields`, `dlg_cursors`, `dlg_buttons`, `dlg_focus`, `dlg_danger`, `dlg_action`, `dlg_hist_keys`/`dlg_hist_idx`/`dlg_saved`.
- `handle_dialog_key`: `Tab`/`Shift-Tab` cycle focus over fields then buttons; `Enter` confirms (or cancels on the last button); `ESC` cancels; `Left`/`Right` switch buttons; on any field with a history key `Up`/`Down` walk that field's history (readline-style: first `Up` = most recent). Convention: the last button cancels, any other confirms.

### Per-field input history

- Every dialog field can carry its own history. `open_dialog(..., hist_keys=[...])` takes one key per field (`""` = no history). `_dlg_confirm` records each non-empty field value into `App.input_history[key]` (newest-first, deduped, capped at 20) before running the action.
- `input_history: dict[str, list[str]]` is persisted in `~/.xc/xc.json`. Legacy two-slot `copy_history` is migrated on load to `copy.src` / `copy.dst`. `add_input_history(key, val)` maintains a list.
- Keys in use: copy/move single file → `copy.src`, `copy.dst`; tagged → `copy.dst`; `show_prompt` → `prompt.<slug>` via `slugify(title)` (e.g. mkdir → `prompt.mkdir`, rename → `prompt.rename_to`), so each prompt type has independent recall.
- `_dlg_history_items` returns the focused field's recent entries (newest-first, up to 8, unfiltered — pre-filled field text is NOT used as a filter); `_dlg_history_prev`/`_dlg_history_next` navigate them.
- When the focused field has history, a dropdown is drawn below the dialog box (`_draw_dlg_history`, with its own shadow) showing the entries; the active entry is highlighted while navigating with `Up`/`Down`. This makes available history visible without pressing a key.
- `draw_dialog` centers the box, computes size from content, draws the shadow then the box (`_draw_box` now takes an optional `attr`), renders input fields with `CP_STATUS`, and reverse-highlights the focused button.

### Group operation progress / interrupt

- Copy/move/delete of multiple (or recursive) files report the current file in the status line via `App.progress(msg)`, which draws the message and calls `poll_cancel()`.
- `poll_cancel()` does a non-blocking `getch()` (under `nodelay(True)`); a bare ESC sets `self.op_cancelled`. `_copy_file`/`do_delete` call `progress()`; `_copy_dir` and the tagged/move loops check `op_cancelled` between items and bail early.
- On move, the source delete phase is skipped entirely if the copy was cancelled (no partial-copy data loss).
- `finish_op()` resets `op_cancelled` and sets the err line to `cancelled` when interrupted.

### Env variables viewer (`k`)

- `list_current_env()` returns `sorted(os.environ.items())`.
- Modal state on `App`: `envv_mode`, `envv_list`, `envv_cursor`, `envv_offset`.
- List rows render as `KEY=value` middle-truncated via `shorten_middle()`; below the list the full value wraps across a fixed-height area. An overflow indicator (`... +N chars`) appears on the last line if the value is longer than the full-value area.
- Binds `k` as a keymap (was previously vim-style "cursor up" in the main panel; use `Up`/`Ctrl-P` for that now).

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
