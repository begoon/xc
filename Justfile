default: check build install

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

install:
    cp xc $HOME/bin/
