default: install

run:
    uv run ./xc.py

lint:
    bunx markdownlint-cli --disable line-length -- **/README.md

install:
    cp xc.py $HOME/bin/xc

install-vmi:
    scp xc.py vmi:.local/bin/xc

version new="":
    #!/usr/bin/env python3
    import sys, re; sys.path.insert(0, ".")
    from xc import VERSION, _parse_version

    new = "{{ new }}"
    if not new:
        print(VERSION)
        sys.exit(0)

    old_version = _parse_version(f'VERSION = "{VERSION}"')

    if new in ("+1", "+2", "+3"):
        parts = list(old_version)
        level = int(new[1])
        if level == 1:
            parts[2] += 1
        elif level == 2:
            parts[1] += 1
            parts[2] = 0
        elif level == 3:
            parts[0] += 1
            parts[1] = 0
            parts[2] = 0
        new = ".".join(str(x) for x in parts)

    new_version = _parse_version(f'VERSION = "{new}"')
    if new_version <= old_version:
        sys.exit(f"new version {new} must be higher than current {VERSION}")

    path = "xc.py"
    text = open(path).read()
    text = re.sub(
        r'^(VERSION\s*=\s*["\'])[^"\']+(["\'])',
        rf'\g<1>{new}\2',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    open(path, "w").write(text)
    print(f"{VERSION} -> {new}")

push:
    #!/usr/bin/env python3
    import sys
    sys.path.insert(0, ".")
    from xc import _parse_version, _fetch_remote, VERSION

    remote_text = _fetch_remote()

    remote_version = _parse_version(remote_text)
    local_version = _parse_version(f'VERSION = "{VERSION}"')
    rv = ".".join(str(x) for x in remote_version)
    lv = ".".join(str(x) for x in local_version)
    print(f"local {lv}, remote {rv}")
    if local_version <= remote_version:
        sys.exit(f"local version {lv} must be higher than remote {rv}")

    import subprocess
    subprocess.run(["git", "push"], check=True)
