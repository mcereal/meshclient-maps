#!/bin/sh
# Fetches the fonts (glyph PBFs) and sprites the Protomaps style draws with into build/assets.
set -eu

# Pinned so a rebuild draws the same labels. Bump deliberately.
ASSETS_REF=${ASSETS_REF:-main}
root=$(cd "$(dirname "$0")/.." && pwd)
dest="$root/build/assets"

if [ -d "$dest/fonts" ] && [ -d "$dest/sprites/v4" ]; then
    exit 0
fi
rm -rf "$dest"
git clone -q --depth 1 --branch "$ASSETS_REF" --filter=blob:none --sparse \
    https://github.com/protomaps/basemaps-assets "$dest"
git -C "$dest" sparse-checkout set fonts sprites/v4
