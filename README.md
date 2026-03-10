# xc

A two-panel console file manager inspired by Midnight Commander, written in Go.

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
