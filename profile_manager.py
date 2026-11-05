"""Apply a rendering profile only after the supervisor has stopped this prefix."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET


def atomic_bytes(path: Path, data: bytes, mode: int = 0o600):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.quantower-', delete=False) as f:
        temporary = Path(f.name)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def file_state(path: Path):
    if path.is_symlink():
        return ('symlink', os.readlink(path))
    if path.exists():
        return ('file', path.read_bytes(), path.stat().st_mode & 0o777)
    return ('absent',)


def restore(path: Path, state):
    if state[0] == 'file':
        atomic_bytes(path, state[1], state[2])
    elif state[0] == 'symlink':
        temporary = path.with_name(path.name + '.quantower-link')
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(state[1])
        os.replace(temporary, path)
    else:
        path.unlink(missing_ok=True)


def no_running_prefix(prefix: Path):
    expected = os.fsencode('WINEPREFIX=' + str(prefix))
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            if expected in (proc / 'environ').read_bytes().split(b'\0'):
                raise RuntimeError('The dedicated Wine prefix still has running processes.')
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            # systemd, PAM and ssh-agent may be nondumpable even for their owner.
            # A protected Windows/Wine process is ambiguous and must block edits.
            try:
                name = (proc / 'comm').read_text().strip().lower()
                arg0 = (proc / 'cmdline').read_bytes().split(b'\0', 1)[0]
                executable = arg0.decode(errors='replace').replace('\\', '/').rsplit('/', 1)[-1].lower()
            except OSError:
                raise RuntimeError('Cannot establish ownership of a protected process.')
            if name.endswith('.exe') or executable.endswith('.exe') or name in ('wine', 'wine64', 'wineserver', 'wineserver64'):
                raise RuntimeError('Cannot establish ownership of a protected Wine process.')


def load(base: Path):
    p = json.loads((base / 'runtime-profile.json').read_text())
    if (not isinstance(p, dict) or type(p.get('version')) is not int or
            p.get('version') != 1 or p.get('mode') not in ('gpu', 'compatible')):
        raise RuntimeError('Invalid runtime rendering profile.')
    manifest = json.loads((base / 'runtime-components/manifest.json').read_text())
    if not isinstance(manifest, dict) or not isinstance(manifest.get('required_files'), dict):
        raise RuntimeError('Invalid runtime component manifest.')
    for relative, expected in manifest['required_files'].items():
        if (not isinstance(relative, str) or not relative or Path(relative).is_absolute() or
                '..' in Path(relative).parts or not isinstance(expected, str) or
                not re.fullmatch(r'[0-9a-f]{64}', expected)):
            raise RuntimeError('Invalid runtime component path or checksum.')
        path = base / relative
        if not path.resolve().is_relative_to(base.resolve()):
            raise RuntimeError('A dedicated runtime component escapes its installation: ' + relative)
        if not path.is_file():
            raise RuntimeError('A dedicated runtime component failed verification: ' + relative)
        with path.open('rb') as component:
            digest = hashlib.file_digest(component, 'sha256').hexdigest()
        if digest != expected:
            raise RuntimeError('A dedicated runtime component failed verification: ' + relative)
    if p['mode'] == 'gpu' and not (base / 'gamescope/files/bin/gamescope-brokey').is_file():
        raise RuntimeError('The dedicated graphics compositor is missing.')
    return p['mode']


def registry_dword(text, key, name, value):
    """Set a DWORD in Wine's registry text, accepting clean-prefix missing defaults."""
    header = '[' + key + ']'
    # Registry key/value names are case-insensitive, including when their
    # spelling differs in an imported or hand-edited Wine registry file.
    sections = list(re.finditer(r'(?im)^' + re.escape(header) + r'[^\n]*(?:\n|$)', text))
    if len(sections) > 1:
        raise RuntimeError('Duplicate Wine registry section.')
    line = '"' + name + '"=dword:' + format(value, '08x')
    if not sections:
        return text.rstrip() + '\n\n' + header + '\n' + line + '\n'
    start = sections[0].end()
    following = re.search(r'(?m)^\[', text[start:])
    end = start + following.start() if following else len(text)
    content = text[start:end]
    expression = r'(?im)^"' + re.escape(name) + r'"=.*$'
    found = re.findall(expression, content)
    if len(found) > 1:
        raise RuntimeError('Duplicate Wine registry value.')
    content = re.sub(expression, lambda _: line, content) if found else content.rstrip() + '\n' + line + '\n\n'
    return text[:start] + content + text[end:]


def prepare(base: Path, mode: str):
    if mode not in ('gpu', 'compatible'):
        raise RuntimeError('Unknown rendering profile.')
    prefix = base / 'prefix'
    no_running_prefix(prefix)
    settings = prefix / 'drive_c/AMPQuantowerInstalled/Settings/settings.xml'
    registry = prefix / 'user.reg'
    d3d9 = prefix / 'drive_c/windows/system32/d3d9.dll'
    for path in (settings, registry):
        if path.is_symlink() or prefix.resolve() not in path.resolve().parents:
            raise RuntimeError('A profile settings file is redirected outside its prefix.')
    if d3d9.parent.resolve() != prefix / 'drive_c/windows/system32':
        raise RuntimeError('The graphics DLL directory is redirected.')
    prior = {path: file_state(path) for path in (settings, registry, d3d9)}
    if prior[registry][0] == 'absent':
        raise RuntimeError('The dedicated Wine registry is missing; initialize the prefix first.')
    new_settings = None
    if prior[settings][0] != 'absent':
        tree = ET.fromstring(prior[settings][1])
        matches = [item for item in tree.iter('Item')
                   if item.findtext('Name') == 'SettingsManager.View.UseSoftwareRenderMode']
        if len(matches) > 1 or (matches and matches[0].find('Value') is None):
            raise RuntimeError('Ambiguous application rendering setting.')
        if matches:
            matches[0].find('Value').text = 'false' if mode == 'gpu' else 'true'
            new_settings = ET.tostring(tree, encoding='utf-8', xml_declaration=True)
    text = prior[registry][1].decode('utf-8')
    dpi = 96 if mode == 'gpu' else 192
    for key in (r'Control Panel\\Desktop', r'Software\\Wine\\Fonts'):
        text = registry_dword(text, key, 'LogPixels', dpi)
    # This documented WPF setting also covers first launch before vendor settings
    # exist; no synthetic vendor XML schema is created.
    text = registry_dword(text, r'Software\\Microsoft\\Avalon.Graphics', 'DisableHWAcceleration',
                          0 if mode == 'gpu' else 1)
    new_registry = text.encode('utf-8')
    backups = base / 'logs/runtime-profile-originals'
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backups, 0o700)
    # Keep the first original DLL (including a symlink) for compatible-mode rollback.
    dll_backup = backups / 'd3d9.dll'
    absent_marker = backups / 'd3d9.originally-absent'
    if not dll_backup.exists() and not dll_backup.is_symlink() and not absent_marker.exists():
        if prior[d3d9][0] == 'absent':
            absent_marker.touch(mode=0o600)
        else:
            shutil.copy2(d3d9, dll_backup, follow_symlinks=False)
    for path, name in ((settings, 'settings.xml'), (registry, 'user.reg')):
        if prior[path][0] != 'absent' and not (backups / name).exists():
            atomic_bytes(backups / name, prior[path][1])
    try:
        if mode == 'gpu':
            atomic_bytes(d3d9, (base / 'runtime-components/d3d9.dll').read_bytes(), 0o644)
        elif absent_marker.exists():
            d3d9.unlink(missing_ok=True)
        else:
            restore(d3d9, file_state(dll_backup))
        if new_settings is not None and new_settings != prior[settings][1]:
            atomic_bytes(settings, new_settings)
        if new_registry != prior[registry][1]:
            atomic_bytes(registry, new_registry)
        atomic_bytes(base / 'logs/applied-runtime-profile.json',
                     (json.dumps({'version': 1, 'mode': mode, 'dpi': dpi}) + '\n').encode())
    except BaseException:
        for path, state in prior.items():
            restore(path, state)
        raise
