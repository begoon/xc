default: install

lint:
    bunx markdownlint-cli --disable line-length -- **/README.md

install:
    cp xc.py $HOME/bin/xc

install-vmi:
    scp xc.py vmi:.local/bin/xc
