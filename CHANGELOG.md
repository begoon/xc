# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project loosely follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/begoon/xc/compare/v0.2.34...HEAD
[0.2.34]: https://github.com/begoon/xc/compare/e8e84c9...v0.2.34
[0.2.33]: https://github.com/begoon/xc/tree/e8e84c9
[0.2.21]: https://github.com/begoon/xc/compare/v0.2.20...v0.2.21
[0.2.20]: https://github.com/begoon/xc/releases/tag/v0.2.20
