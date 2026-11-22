#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 pc/generate_config.py
mkdir -p .deps
fetch_pinned() {
  local url="$1" dir="$2" commit="$3"
  if [[ ! -d "$dir/.git" ]]; then
    git init "$dir"
    git -C "$dir" remote add origin "$url"
  fi
  if ! git -C "$dir" cat-file -e "$commit^{commit}" 2>/dev/null; then
    git -C "$dir" fetch --depth 1 "$url" "$commit"
  fi
  git -C "$dir" checkout --detach "$commit"
}
if [[ -z "${PICO_SDK_PATH:-}" ]]; then
  export PICO_SDK_PATH="$PWD/.deps/pico-sdk"
  if [[ ! -d "$PICO_SDK_PATH/.git" ]]; then
    git clone --depth 1 --branch 2.2.0 https://github.com/raspberrypi/pico-sdk.git "$PICO_SDK_PATH"
  fi
  git -C "$PICO_SDK_PATH" submodule update --init --recursive
fi
fetch_pinned https://github.com/raspberrypi/FreeRTOS-Kernel.git "$PWD/.deps/FreeRTOS-Kernel" 4f7299d6ea746b27a9dd19e87af568e34bd65b15
fetch_pinned https://github.com/sahlberg/libsmb2.git "$PWD/.deps/libsmb2" c7996bb97601a7f04d3e5c371317425da9b82ba4
cmake -S firmware/smb_probe -B build/smb -G Ninja -DPICO_BOARD=pico2_w -DPICO_SDK_PATH="$PICO_SDK_PATH" -DFREERTOS_KERNEL_PATH="$PWD/.deps/FreeRTOS-Kernel" -DLIBSMB2_PATH="$PWD/.deps/libsmb2"
cmake --build build/smb
echo "PRIVATE UF2: $PWD/build/smb/pico_wifi_display_smb_probe.uf2"
echo "Contains Wi-Fi/SMB credentials; do not upload or share."
