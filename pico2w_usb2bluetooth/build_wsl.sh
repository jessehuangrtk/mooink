#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -z "${PICO_SDK_PATH:-}" ]]; then
  export PICO_SDK_PATH="$HOME/pico/pico-sdk-2.2.0"
  if [[ ! -d "$PICO_SDK_PATH/.git" ]]; then
    mkdir -p "$(dirname "$PICO_SDK_PATH")"
    git clone --branch 2.2.0 --depth 1 https://github.com/raspberrypi/pico-sdk.git "$PICO_SDK_PATH"
  fi
  git -C "$PICO_SDK_PATH" submodule update --init --recursive
fi
cmake -S . -B build -G Ninja -DPICO_BOARD=pico2_w -DPICO_SDK_PATH="$PICO_SDK_PATH"
cmake --build build
test -f build/pico2w_usb2bluetooth.uf2
echo "UF2: $(pwd)/build/pico2w_usb2bluetooth.uf2"
