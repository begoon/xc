#!/bin/sh
set -eu

REPO="https://raw.githubusercontent.com/begoon/xc/main"

echo "downloading xc..."
curl -fLo xc "$REPO/xc.py"
chmod +x xc

echo "starting xc..."
exec ./xc
