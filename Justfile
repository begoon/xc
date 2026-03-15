default: install

lint:
    bunx markdownlint-cli --disable line-length -- **/README.md

install:
    cp xc.py $HOME/bin/xc

install-vmi:
    scp xc.py vmi:.local/bin/xc

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
