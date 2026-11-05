#!/usr/bin/env bash
set -euo pipefail
umask 077

base="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export WINEPREFIX="${base}/prefix"
export WINEARCH=win64
export WINEDLLOVERRIDES='winemenubuilder.exe=d'
unset DOTNET_ROOT DOTNET_ROOT_X64 WINEDLLPATH WINELOADER WINESERVER
unset DOTNET_STARTUP_HOOKS CONNECTION_OBSERVER_PROBE

exec /usr/bin/python3 -u "${base}/diagnostics.py" run --base "$base" "$@"
