# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project loosely follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.44] - 2026-11-26

### Added

- `.7z` archives open as a read-only VFS through the 7-Zip command line
  program (`7zz`, `7z` or `7za`), also from remotes. Modes, modification
  times and symlinks are preserved on copy, and tagged files are extracted in
  one 7-Zip run. Password-protected archives are reported as unsupported.

## [0.2.43] - 2026-11-25

### Added

- Configurable `Enter` associations in `~/.xc/xc.conf` (JSON list of
  `{"ext", "cmd", "pager"}`), checked after built-in behaviour and before the
  built-in associations. Commands use the usual macros, and `%F` on a remote
  VFS uses a temporary local copy.
- The cursor position of both panels is saved on exit and restored on start.

### Fixed

- A panel inside an archive or remote at exit reopens in the local directory
  it was entered from, with the cursor on that file, instead of falling back
  to the start directory.

## [0.2.42] - 2026-11-25

### Added

- `mkdir` puts the cursor on the new directory (the first part of a nested
  path).
- `b` `b` bookmark jumps to `~/Dropbox`.

### Fixed

- `Enter` after `/` quick search opens symlinks to directories (e.g.
  `~/Dropbox`) instead of putting the name on the command line.

## [0.2.41] - 2026-11-24

### Changed

- Google Drive folders show their modified time, so folders with the same
  name (e.g. several `Takeout` exports) can be told apart.

## [0.2.40] - 2026-11-24

### Added

- Google Docs, Sheets, Slides and Drawings are shown as `.docx`, `.xlsx`,
  `.pptx` and `.png` and exported to that format when copied or viewed.
  Forms and other types without an export format report a clear error, and
  native files cannot be overwritten by an upload.
- Status-line errors are also written to `~/.xc/xc.log`.
- `export=office|pdf|open|text` in a `.gdrive` file chooses the export
  format for Google files, and `export.docs`, `export.sheets`,
  `export.slides` and `export.drawings` override it per type (e.g.
  `export.sheets=csv`).
- `Enter` on `.docx`, `.xlsx`, `.pptx`, `.odt`, `.ods` and `.odp` files opens
  them as zip archives (when they really are ZIP files).
- Archives inside remote VFS (Google Drive, Dropbox, SSH, ...) can be entered:
  xc works on a temporary copy that is deleted when leaving or exiting.

## [0.2.39] - 2026-11-24

### Added

- Google Drive OAuth login: with `client_id`/`client_secret` (and no `key`)
  in a `.gdrive` file, xc logs in through the browser (loopback redirect with
  PKCE). The token lasts about 1 hour and is kept in memory only, like
  Dropbox: the time left is shown in the panel header, it is revoked on exit,
  and `logout gdrive:<account>` in the `r` menu revokes it.
- `client_secret` may be a 1Password `op://` reference, resolved with
  `op read` when a login is needed.
- A `.gdrive` without `folder` opens on a virtual top level with `My Drive`,
  `Shared with me` and `Shared drives`. `folder=root` starts in My Drive.
- Google Drive shortcuts are followed, so folder shortcuts can be entered.
  Items with the same name are shown as `name (2).ext`, `name (3).ext`, ...

### Changed

- Google Drive and Dropbox API errors are shown as short messages in the
  status line (e.g. Google's own explanation) instead of raw exceptions.
- `.gdrive` paths are resolved by listing parent folders, so names with
  quotes and duplicates are handled correctly.

## [0.2.38] - 2026-11-24

### Added

- Dropbox remote: `.dropbox` config files (`type=dropbox`, `app_key=...`,
  optional `root`, `account`, `team=1`) browse Dropbox through its API, not
  the local sync folder. Login uses the OAuth2 PKCE copy-paste flow in the
  browser, with no app secret.
- The Dropbox login is a short-lived (about 4 hours) token kept in memory
  only, never written to disk. The panel header shows the time left, live
  tokens are revoked when xc exits, and `logout dropbox:<account>` in the `r`
  remotes menu revokes it explicitly and returns the panel to the local
  directory.

### Fixed

- The `r` remotes menu now also lists `.oci` and `.gdrive` files (and
  `.dropbox`), not only `.s3`, `.gcs` and `.ssh`.

## [0.2.37] - 2026-11-23

### Added

- `=` changes the inactive panel to the active panel's directory, leaving any
  archive or remote VFS it was in. It works only when the active panel shows a
  local directory.

## [0.2.36] - 2026-11-21

### Changed

- The shell installer looks for `~/bin` first, then `~/.local/bin`. If neither
  directory exists, it saves `~/xc` and prints Bash setup instructions.
- Refresh the README screenshot and resolve the PyPI token through 1Password
  when running `just publish`.

## [0.2.35] - 2026-11-21

### Changed

- Restore both panel directories and the active side on startup. Command-line
  folders override the active panel first and the other panel second.
- Save the session when quitting with a second Ctrl-C.

## [0.2.34] - 2026-09-21

### Fixed

- Advance the version so installations of the older GitHub code labelled
  `0.2.33` can self-update to the file metadata fixes.
- Record the current PyPI releases and associate this release with the
  `v0.2.34` Git tag.

## [0.2.33] - 2026-09-21

This is the PyPI publication date. GitHub code had already used `0.2.33`
since June 2, before the metadata fixes were added. The published wheel
and source archive match `xc.py` at commit `e8e84c9`.

### Fixed

- Preserve local file permissions, timestamps, ownership, symlinks, and
  supported native metadata during copying. Use native renames for local
  moves when possible; retain move sources if metadata preservation fails.
- Restore stored modes, timestamps, and symlinks during TAR/ZIP extraction,
  including tagged extraction, and preserve modes and timestamps over SSH.
- Preserve executable permissions in remote-command temporary files and
  upload SSH mode changes. Document backend metadata limitations.

### Added

- Regression tests for metadata preservation and move safety.
- Optional `.env` loading and a `PYPI_TOKEN` default for `just publish`.

### Changed

- Earlier GitHub changes included overwrite confirmation for copy, move,
  and rename; command-line panel directories; modal input dialogs with
  history; cancellable group operations; and navigation out of unreadable
  directories. These changes were not previously recorded here.

## [0.2.26] - 2026-04-24

### Changed

- Help panel (`h`) is now laid out in two columns (Navigation+Menus,
  Commands+Viewers+Other), reducing its height.

## [0.2.25] - 2026-04-24

### Added

- `k` opens an environment variables viewer modal showing all vars from
  the current process environment, sorted by key; long values are
  middle-truncated with `...` in the list, and the full value of the
  selected variable is displayed below the list.
- `Ctrl-R` refreshes the env list.

### Changed

- `k` is no longer bound to vim-style "cursor up" in the main panel
  (use `Up` or `Ctrl-P`). It now opens the env viewer.

## [0.2.24] - 2026-04-24

### Changed

- In the processes modal (list and env sections) and in the PATH viewer
  (paths and executables modals), `Left` / `Right` now act as
  `PgUp` / `PgDn` for consistency with the main panels.

## [0.2.23] - 2026-04-24

### Added

- `o` opens a PATH viewer modal listing each entry in `$PATH` with the
  number of executable files it contains, or `missing` if the directory
  does not exist.
- Pressing Enter on an existing PATH entry opens a second scrollable
  modal listing the executables in that directory.
- `Ctrl-R` refreshes the PATH listing.

## [0.2.22] - 2026-04-24

### Added

- `p` opens a process modal listing all running processes with PID, user,
  command (middle-truncated with `...`), and listening TCP/UDP ports.
- Filter input at the top: space-separated words all must match command
  or ports (or pid/user); words prefixed with `-` exclude matches.
- Full command line of the selected process is shown below the list.
- Environment variables of the selected process are shown below the
  command line (via `/proc/<pid>/environ` on Linux, `ps eww` on macOS).
- `Tab` cycles focus between filter, list and env sections; when env is
  focused, arrow/PgUp/PgDn/Home/End scroll the env list.
- `k` (in list focus) or `Ctrl-K` (any focus) sends SIGKILL to the
  selected process after a `y/N` confirmation.
- `Ctrl-R` refreshes the process list.

## [0.2.21] - 2026-04-23

### Changed

- In `/` type-search mode, Enter now steps into the item if it is a
  directory; for files the behaviour is unchanged (puts the name on the
  command line).
- Removed the `v` view menu; its items are folded into a combined
  edit/view menu on `e`: `e` vi, `m` mcedit, `v` mcview, `l` less,
  `j` jq, `x` xxd (plus `c` cot on macOS).

## [0.2.20]

Rough backfill reconstructed from git history. Dates and version
boundaries are approximate — only the current version is tracked
accurately.

### Added

- Two-panel console file manager in a single `xc.py` uv script.
- SSH VFS (`SSHFS`).
- Compressed/archive VFS (tar, tar.gz, tar.xz, zip).
- Grep/search VFS (`GrepFS`) with incremental results and panelised view.
- Remotes with `~`/`$HOME` resolution in `key=` values.
- OCI Object Storage VFS (`OCIFS`).
- Google Drive VFS (`GDriveFS`).
- File associations (including Linux).
- Command line history.
- Help screen.
- Self-update and version check.
- Install script.
- Publish to PyPI.
- Colouring for dotfiles, directories, and extensions; nc/mc-style
  palette.

### Changed

- Search results now open in a panel.
- Editor associations moved to a dedicated editors list.
- Various README updates and `CLAUDE.md` documentation.

### Fixed

- `tar.gz` read performance.
- Install script issues.
- Miscellaneous small fixes.

[Unreleased]: https://github.com/begoon/xc/compare/v0.2.36...HEAD
[0.2.36]: https://github.com/begoon/xc/compare/v0.2.35...v0.2.36
[0.2.35]: https://github.com/begoon/xc/compare/v0.2.34...v0.2.35
[0.2.34]: https://github.com/begoon/xc/compare/e8e84c9...v0.2.34
[0.2.33]: https://github.com/begoon/xc/tree/e8e84c9
[0.2.21]: https://github.com/begoon/xc/compare/v0.2.20...v0.2.21
[0.2.20]: https://github.com/begoon/xc/releases/tag/v0.2.20
