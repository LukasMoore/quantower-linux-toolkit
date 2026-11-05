#!/usr/bin/env bash
set -euo pipefail
umask 077
base="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Internal helper: launch.sh supplies ownership and full diagnostics.
# Python parses JSON into argv; no configuration value is evaluated by a shell.
exec python3 "${base}/runtime_config.py" render --base "$base"
