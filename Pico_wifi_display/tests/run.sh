#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cc -std=c11 -Wall -Wextra -Werror -Ifirmware tests/test_mtp_codec.c firmware/mtp_codec.c -o "$tmp/test_mtp"
cc -std=c11 -Wall -Wextra -Werror -Ifirmware tests/test_mtp_stream.c firmware/mtp_codec.c firmware/mtp_stream.c -o "$tmp/test_stream"
"$tmp/test_stream"
"$tmp/test_mtp"
cc -std=c11 -Wall -Wextra -Werror -Ifirmware tests/test_display_menu.c firmware/display_menu.c -o "$tmp/test_menu"
"$tmp/test_menu"
cc -std=c11 -Wall -Wextra -Werror -Ifirmware tests/test_smb_snapshot.c firmware/smb_snapshot.c -o "$tmp/test_snapshot"
"$tmp/test_snapshot"
cc -std=c11 -Wall -Wextra -Werror -Ifirmware tests/test_debug_log.c firmware/debug_log.c -o "$tmp/test_log"
"$tmp/test_log"
python3 -m unittest discover -s tests -p 'test_*.py' -v
