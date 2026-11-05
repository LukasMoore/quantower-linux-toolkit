"""Resolve the explicitly selected official AMP application version."""
from dataclasses import dataclass
import json
from pathlib import Path
import re


@dataclass(frozen=True)
class ApplicationRuntime:
    version: str
    directory: Path
    windows_executable: str


def load(base):
    base = Path(base)
    selection = base / 'application-version.json'
    version = '1.146.7'  # Compatibility with the original installation/configuration.
    if selection.exists():
        record = json.loads(selection.read_text())
        if not isinstance(record, dict) or record.get('schema_version') != 1:
            raise ValueError('Invalid application version selection.')
        version = record.get('application_version')
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Invalid AMP application version.')
    directory = base / 'prefix/drive_c/AMPQuantowerInstalled/TradingPlatform' / ('v' + version)
    return ApplicationRuntime(version, directory,
                              rf'C:\AMPQuantowerInstalled\TradingPlatform\v{version}\Starter.exe')
