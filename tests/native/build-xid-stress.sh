#!/usr/bin/env bash
# Optional display-dependent native regression probe; never starts Quantower.
set -euo pipefail
[[ $# == 2 ]] || { echo "Usage: $0 extracted-dev-root output-binary" >&2; exit 2; }
xid_dependencies="$(realpath -- "$1")"
xid_source="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/xid-stress.c"
cc -std=c11 -O2 -g -pthread -Wall -Wextra -Wno-unused-parameter \
  -I"${xid_dependencies}/usr/include" "$xid_source" -Wl,-l:libX11.so.6 -o "$2"
