#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cc -std=c11 -Wall -Wextra -Werror -Ifirmware tests/test_mtp_codec.c firmware/mtp_codec.c -o "$tmp/test_mtp"
"$tmp/test_mtp"
python3 -m unittest discover -s tests -p 'test_*.py' -v
