#!/usr/bin/env bash
# usage: setup_sandbox.sh <sanitized wine prefix containing Proteus 8>  -> sandbox/prefix-pristine
# Prefix must have no symlinks into the real $HOME and no dosdevices/z: (see lessons wine-sandbox-mc9 #198).
set -euo pipefail
D="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$D/sandbox/exchange"
[ -e "$D/sandbox/prefix-pristine" ] && { echo "prefix-pristine exists"; exit 1; }
cp -a --reflink=auto "$1" "$D/sandbox/prefix-pristine"
echo "bad links:"; find "$D/sandbox/prefix-pristine" -type l -lname '/home*' | head
