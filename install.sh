#!/bin/sh
set -eu

REPO="https://raw.githubusercontent.com/begoon/xc/main"

install_dir="$HOME"
for dir in "$HOME/bin" "$HOME/.local/bin"; do
    if [ -d "$dir" ]; then
        install_dir="$dir"
        break
    fi
done

target="$install_dir/xc"
if [ -d "$target" ]; then
    echo "Cannot install xc: $target is a directory" >&2
    exit 1
fi

echo "downloading xc..."
tmp=$(mktemp "$install_dir/.xc.XXXXXXXX")
trap 'rm -f "$tmp"' 0
curl -fLo "$tmp" "$REPO/xc.py"
chmod 755 "$tmp"
mv -f "$tmp" "$target"
echo "Installed xc at $target"

if [ "$install_dir" = "$HOME" ]; then
    cat <<'EOF'

To run xc from any directory in Bash, we recommend:

mkdir -p ~/.local/bin
mv ~/xc ~/.local/bin/xc
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
xc
EOF
fi
