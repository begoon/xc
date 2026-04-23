# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project loosely follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/begoon/xc/compare/v0.2.21...HEAD
[0.2.21]: https://github.com/begoon/xc/compare/v0.2.20...v0.2.21
[0.2.20]: https://github.com/begoon/xc/releases/tag/v0.2.20
