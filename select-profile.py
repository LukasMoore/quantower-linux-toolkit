#!/usr/bin/env python3
"""Select rendering for the next launch without touching a running session."""
import argparse
import json
from pathlib import Path
from profile_manager import atomic_bytes

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('mode', choices=['gpu', 'compatible'])
p.add_argument('--base', type=Path, default=Path(__file__).resolve().parent)
a = p.parse_args()
base = a.base.resolve()
if not (base / 'prefix/drive_c/AMPQuantowerInstalled').is_dir():
    p.error('The dedicated Quantower installation is not present.')
atomic_bytes(base / 'runtime-profile.json',
             (json.dumps({'version': 1, 'mode': a.mode}) + '\n').encode())
print('Selected ' + a.mode + ' rendering for the next launch.')
