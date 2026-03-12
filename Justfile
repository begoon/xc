default: install-py

default-go: check build install-go

build:
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o xc main.go

lint:
    bunx markdownlint-cli --disable line-length -- **/README.md

prerequisites:
    go install github.com/kisielk/errcheck@latest
    go install mvdan.cc/gofumpt@latest
    go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest

check:
    gofumpt -l -w .
    golangci-lint run ./...
    errcheck ./...

install-go:
    cp xc $HOME/bin/

install-py:
    cp xc.py $HOME/bin/xc

install-vmi:
    scp xc.py vmi:.local/bin/xc

