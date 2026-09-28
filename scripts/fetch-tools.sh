#!/bin/sh
# Downloads the pmtiles CLI into build/tools, for this host's OS and architecture.
set -eu

PMTILES_VERSION=1.31.2
root=$(cd "$(dirname "$0")/.." && pwd)
dest="$root/build/tools"
mkdir -p "$dest"

if [ -x "$dest/pmtiles" ] && "$dest/pmtiles" version 2>/dev/null | grep -q "$PMTILES_VERSION"; then
    exit 0
fi

case "$(uname -s)" in
    # The two platforms' assets are not named alike: "go-pmtiles-" on macOS, "go-pmtiles_" on Linux.
    Darwin) os=Darwin; ext=zip; sep=- ;;
    Linux) os=Linux; ext=tar.gz; sep=_ ;;
    *) echo "fetch-tools: unsupported OS $(uname -s)" >&2; exit 1 ;;
esac
case "$(uname -m)" in
    arm64 | aarch64) arch=arm64 ;;
    x86_64 | amd64) arch=x86_64 ;;
    *) echo "fetch-tools: unsupported CPU $(uname -m)" >&2; exit 1 ;;
esac

url="https://github.com/protomaps/go-pmtiles/releases/download/v$PMTILES_VERSION/go-pmtiles${sep}${PMTILES_VERSION}_${os}_${arch}.$ext"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL -o "$tmp/pmtiles.$ext" "$url"
if [ "$ext" = zip ]; then
    unzip -q -o "$tmp/pmtiles.$ext" pmtiles -d "$tmp"
else
    tar -xzf "$tmp/pmtiles.$ext" -C "$tmp" pmtiles
fi
mv "$tmp/pmtiles" "$dest/pmtiles"
chmod +x "$dest/pmtiles"
"$dest/pmtiles" version
