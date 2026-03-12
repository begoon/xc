# xc

A two-panel console file manager inspired by Midnight Commander, written in Python.

## Intro

The Python version of xc is a single self-contained script (`xc.py`) that runs via [uv](https://docs.astral.sh/uv/). This means:

- **Zero setup** -- no virtualenv, no `pip install`, no `requirements.txt`. Just run `uv run xc.py`.
- **Inline dependencies** -- the script header declares its own dependencies (`boto3`, `google-cloud-storage`), and uv resolves and caches them automatically on the first run.
- **Reproducible** -- uv pins the Python version (`>=3.11`) and handles isolation, so the script works the same way on any machine.
- **Single file to deploy** -- copy `xc.py` to a server, a dotfiles repo, or a USB stick. There is nothing else to carry.

### Installing uv

```sh
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Homebrew
brew install uv

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

After installing, run the file manager with:

```sh
uv run xc.py
```

The script has a shebang line, so you can rename it, make it executable, and put it on your PATH:

```sh
cp xc.py ~/.local/bin/xc
chmod +x ~/.local/bin/xc
xc
```

## Macros

Menu actions and the `Run` method support macro expansion in command strings. Macros are prefixed with `%`.

| Macro | Description                                              |
|-------|----------------------------------------------------------|
| `%f`  | Current file name                                        |
| `%F`  | Current file full path                                   |
| `%x`  | Current file name without extension                      |
| `%X`  | Current file full path without extension                 |
| `%d`  | Current directory name                                   |
| `%D`  | Current directory full path                              |
| `%m`  | Tagged file names (space-separated, shell-quoted)        |
| `%M`  | Tagged file full paths (space-separated, shell-quoted)   |
| `%&`  | Run command in the background (no terminal output)       |

### Quoting

All macros are automatically shell-quoted. To disable quoting, prefix the macro letter with `~`:

| Macro  | Description                 |
|--------|-----------------------------|
| `%~f`  | File name, unquoted         |
| `%~F`  | File path, unquoted         |
| `%~m`  | Tagged file names, unquoted |
| `%~M`  | Tagged file paths, unquoted |

### Examples

```text
vi %F          →  vi '/home/user/hello world.txt'
less %F        →  less '/home/user/notes.md'
tar czf %x.tar.gz %~f  →  tar czf 'mydir.tar.gz' mydir
echo %m        →  echo 'file1.txt' 'file2.txt'
cp %M /tmp %&  →  cp '/home/user/a.txt' '/home/user/b.txt' /tmp  (background)
```
