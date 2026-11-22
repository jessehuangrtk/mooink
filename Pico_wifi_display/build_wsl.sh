#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p .deps
if [[ -z "${PICO_SDK_PATH:-}" ]]; then
  export PICO_SDK_PATH="$PWD/.deps/pico-sdk"
  if [[ ! -d "$PICO_SDK_PATH/.git" ]]; then
    git clone --depth 1 --branch 2.2.0 https://github.com/raspberrypi/pico-sdk.git "$PICO_SDK_PATH"
  fi
  git -C "$PICO_SDK_PATH" submodule update --init --recursive
fi
cmake -S firmware -B build -G Ninja -DPICO_BOARD=pico2_w -DPICO_SDK_PATH="$PICO_SDK_PATH"
cmake --build build
echo "UF2: $PWD/build/pico_wifi_display_probe.uf2"

echo "CDC menu preview: $PWD/build/pico_wifi_display_menu.uf2"
