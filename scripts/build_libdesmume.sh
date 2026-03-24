#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
INTERFACE_DIR="$PROJECT_ROOT/desmume-src/desmume/src/frontend/interface"

cd "$INTERFACE_DIR"

# Configure (only needed once)
if [ ! -d build ]; then
    echo "Configuring meson build..."
    meson setup build
fi

# Build
echo "Building libdesmume.so..."
ninja -C build

# Create build dir in project root and symlink
mkdir -p "$PROJECT_ROOT/build"
BUILT_LIB=$(find build -name 'libdesmume.so*' -type f | head -1)
if [ -z "$BUILT_LIB" ]; then
    echo "ERROR: libdesmume.so not found after build"
    exit 1
fi

ln -sf "$INTERFACE_DIR/$BUILT_LIB" "$PROJECT_ROOT/build/libdesmume.so"
echo "Done! Library at: $PROJECT_ROOT/build/libdesmume.so"
