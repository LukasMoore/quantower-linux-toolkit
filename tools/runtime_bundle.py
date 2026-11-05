"""Data-only runtime bundle integrity, deterministic archiving and safe extraction."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import tarfile
import tempfile

MANIFEST = 'bundle-manifest.json'
MAX_MANIFEST = 32 * 1024 * 1024
MAX_FILES = 100000
MAX_BYTES = 32 * 1024 ** 3
REQUIRED_COMPONENTS = ('wine', 'gamescope', 'x11', 'dxvk_d3d9')


def safe_relative(value):
    if (not isinstance(value, str) or not value or '\\' in value or any(ord(c) < 32 for c in value) or
            value.startswith('/') or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('Unsafe bundle-relative path.')
    return PurePosixPath(value)


def link_destination(name, target):
    safe_relative(name)
    if not isinstance(target, str) or not target or target.startswith('/') or '\\' in target or any(ord(c) < 32 for c in target):
        raise ValueError('Bundle links must be relative and contained.')
    result = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
    safe_relative(result)
    return result


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_json(path, maximum=MAX_MANIFEST):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise ValueError('Missing, redirected or oversized bundle metadata: ' + path.name)
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Bundle metadata must be a JSON object.')
    return value


def write_json(path, value):
    data = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
    with Path(path).open('xb') as stream:
        stream.write(data)
    Path(path).chmod(0o644)


def walk(root):
    """Enumerate every entry without following symlink directories."""
    root = Path(root)
    def visit(directory):
        for path in sorted(directory.iterdir(), key=lambda p: p.name):
            relative = path.relative_to(root).as_posix()
            safe_relative(relative)
            yield relative, path
            if path.is_dir() and not path.is_symlink():
                yield from visit(path)
    yield from visit(root)


def inventory(root, *, exclude_manifest=True):
    root = Path(root).resolve(strict=True)
    files = {}
    total = 0
    for name, path in walk(root):
        if exclude_manifest and name == MANIFEST:
            continue
        info = path.lstat()
        if info.st_mode & 0o7000:
            raise ValueError('Privileged/sticky modes are not allowed in runtime bundles.')
        mode = stat.S_IMODE(info.st_mode)
        if path.is_symlink():
            target = os.readlink(path)
            link_destination(name, target)
            if not path.resolve(strict=True).is_relative_to(root):
                raise ValueError('Bundle symlink escapes its root: ' + name)
            files[name] = {'type': 'symlink', 'target': target}
        elif path.is_dir():
            files[name] = {'type': 'directory', 'mode': mode}
        elif path.is_file():
            total += info.st_size
            if total > MAX_BYTES:
                raise ValueError('Bundle exceeds its bounded size.')
            files[name] = {'type': 'file', 'size': info.st_size, 'mode': mode, 'sha256': sha256(path)}
        else:
            raise ValueError('Special filesystem objects are not allowed in bundles: ' + name)
        if len(files) > MAX_FILES:
            raise ValueError('Bundle contains too many entries.')
    return files


def validate_manifest(value):
    if not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('kind') != 'quantower-linux-runtime':
        raise ValueError('Unsupported runtime bundle manifest.')
    if not isinstance(value.get('version'), str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?', value['version']):
        raise ValueError('Invalid runtime bundle version.')
    if value.get('platform') != 'debian13-x86_64':
        raise ValueError('Unknown runtime bundle target platform.')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', str(value.get('application_version', ''))):
        raise ValueError('Runtime bundle must pin an exact application version.')
    root = value.get('archive_root')
    safe_relative(root)
    if '/' in root:
        raise ValueError('Runtime archive root must be one directory name.')
    components = value.get('components')
    files = value.get('files')
    if not isinstance(components, dict) or not isinstance(files, dict) or not files:
        raise ValueError('Runtime bundle components and inventory are required.')
    if len(files) > MAX_FILES:
        raise ValueError('Runtime bundle inventory exceeds its limit.')
    for name in REQUIRED_COMPONENTS:
        if name not in components:
            raise ValueError('Runtime bundle is missing component: ' + name)
    for path in components.values():
        safe_relative(path)
        if not path.startswith('payload/') or path not in files:
            raise ValueError('Component path must identify a declared payload entry.')
    total = 0
    for name, entry in files.items():
        safe_relative(name)
        if name == MANIFEST or not isinstance(entry, dict):
            raise ValueError('Invalid runtime bundle inventory entry.')
        kind = entry.get('type')
        if kind == 'file':
            size = entry.get('size')
            if type(size) is not int or size < 0 or not re.fullmatch(r'[0-9a-f]{64}', str(entry.get('sha256', ''))):
                raise ValueError('Invalid file size or digest in runtime inventory.')
            total += size
        elif kind == 'symlink':
            target = link_destination(name, entry.get('target'))
            if target not in files:
                raise ValueError('Runtime symlink target is absent from inventory.')
        elif kind != 'directory':
            raise ValueError('Unsupported runtime bundle entry type.')
        if kind != 'symlink' and entry.get('mode') not in (0o644, 0o755):
            raise ValueError('Runtime files must use normal read/executable modes.')
        # An archived file must never be written through a symlink ancestor.
        parent = PurePosixPath(name).parent
        while str(parent) != '.':
            if files.get(str(parent), {}).get('type') != 'directory':
                raise ValueError('Runtime inventory parent must be a declared directory.')
            parent = parent.parent
    if total > MAX_BYTES:
        raise ValueError('Runtime bundle inventory exceeds its size limit.')
    for key in ('license_manifest', 'source_offer'):
        name = value.get(key)
        safe_relative(name)
        if files.get(name, {}).get('type') != 'file':
            raise ValueError('Runtime bundle must include its license and source manifests.')
    return value


def verify_bundle(root):
    root = Path(root).resolve(strict=True)
    manifest = validate_manifest(read_json(root / MANIFEST))
    actual = inventory(root)
    # GNU tar can apply a user's restrictive umask while extracting. Reduced
    # group/other access or owner write access is safe; owner read/execute stay
    # required so a read-only extracted runtime also remains usable.
    for name in set(actual) & set(manifest['files']):
        expected = manifest['files'][name]
        if actual[name]['type'] == expected['type'] and expected['type'] != 'symlink':
            mode = actual[name]['mode']
            required = expected['mode'] & 0o500
            if (mode & required) == required and not (mode & ~expected['mode']):
                actual[name]['mode'] = expected['mode']
    if actual != manifest['files']:
        missing = sorted(set(manifest['files']) - set(actual))
        extra = sorted(set(actual) - set(manifest['files']))
        changed = sorted(name for name in set(actual) & set(manifest['files']) if actual[name] != manifest['files'][name])
        detail = (missing or extra or changed)[:3]
        raise ValueError('Runtime bundle integrity mismatch: ' + ', '.join(detail))
    return manifest


def _tar_entries(archive):
    members = archive.getmembers()
    if not members or len(members) > MAX_FILES + 2:
        raise ValueError('Invalid runtime archive entry count.')
    first = members[0].name.rstrip('/')
    safe_relative(first)
    if ('/' in first or not members[0].isdir() or members[0].mode != 0o755 or
            members[0].uid != 0 or members[0].gid != 0 or members[0].uname or members[0].gname):
        raise ValueError('Runtime archive must begin with one root directory.')
    found = {}
    total = 0
    for member in members[1:]:
        if member.uid != 0 or member.gid != 0 or member.uname or member.gname or set(member.pax_headers) - {'path', 'linkpath', 'size'}:
            raise ValueError('Runtime archive contains unexpected ownership or extended metadata.')
        name = member.name.rstrip('/')
        if not name.startswith(first + '/'):
            raise ValueError('Runtime archive contains multiple roots or an escaping member.')
        name = name[len(first) + 1:]
        safe_relative(name)
        if name in found or not (member.isfile() or member.isdir() or member.issym()):
            raise ValueError('Duplicate or unsupported runtime archive member.')
        if member.mode & 0o7000:
            raise ValueError('Privileged archive modes are prohibited.')
        if member.issym():
            link_destination(name, member.linkname)
        total += member.size
        if total > MAX_BYTES:
            raise ValueError('Runtime archive exceeds its size limit.')
        found[name] = member
    return first, found


def _verify_open_archive(archive):
    root_name, entries = _tar_entries(archive)
    item = entries.get(MANIFEST)
    if item is None or not item.isfile() or item.size > MAX_MANIFEST:
        raise ValueError('Runtime archive has no valid manifest.')
    with archive.extractfile(item) as stream:
        manifest = validate_manifest(json.load(stream))
    if root_name != manifest.get('archive_root'):
        raise ValueError('Runtime archive root does not match its manifest.')
    if set(entries) != set(manifest['files']) | {MANIFEST}:
        raise ValueError('Runtime archive inventory does not match its manifest.')
    for name, record in manifest['files'].items():
        member = entries[name]
        kind = record['type']
        if kind == 'directory':
            okay = member.isdir() and member.mode == record['mode']
        elif kind == 'symlink':
            okay = member.issym() and member.linkname == record['target']
        else:
            okay = member.isfile() and member.size == record['size'] and member.mode == record['mode']
            if okay:
                with archive.extractfile(member) as stream:
                    okay = hashlib.file_digest(stream, 'sha256').hexdigest() == record['sha256']
        if not okay:
            raise ValueError('Runtime archive integrity mismatch: ' + name)
    return manifest


def verify_archive(path):
    try:
        with tarfile.open(path, 'r:*') as archive:
            return _verify_open_archive(archive)
    except (tarfile.TarError, EOFError) as error:
        raise ValueError('Invalid runtime archive format.') from error


def archive_bundle(root, output, *, epoch=0):
    root, output = Path(root).resolve(strict=True), Path(output).expanduser().absolute()
    manifest = verify_bundle(root)
    if type(epoch) is not int or not 0 <= epoch < 2 ** 31:
        raise ValueError('Source date epoch must be a nonnegative supported Unix timestamp.')
    if output.exists() or output.is_symlink():
        raise FileExistsError('Runtime archive output must be new.')
    checksum = output.with_name(output.name + '.sha256')
    if checksum.exists() or checksum.is_symlink():
        raise FileExistsError('Runtime archive checksum output must be new.')
    if output.resolve().is_relative_to(root):
        raise ValueError('Runtime archive output must be outside its staged bundle.')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.runtime-bundle-', delete=False) as stream:
            temporary = Path(stream.name)
            with gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=epoch, compresslevel=6) as compressed:
                with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as archive:
                    top = tarfile.TarInfo(manifest['archive_root'])
                    top.type, top.mode, top.mtime = tarfile.DIRTYPE, 0o755, epoch
                    archive.addfile(top)
                    entries = {**manifest['files'], MANIFEST: {'type': 'file', 'mode': 0o644,
                                                             'size': (root / MANIFEST).stat().st_size}}
                    for name in sorted(entries):
                        record = entries[name]
                        item = tarfile.TarInfo(manifest['archive_root'] + '/' + name)
                        item.mtime = epoch
                        if record['type'] == 'directory':
                            item.type, item.mode = tarfile.DIRTYPE, record['mode']
                            archive.addfile(item)
                        elif record['type'] == 'symlink':
                            item.type, item.mode, item.linkname = tarfile.SYMTYPE, 0o777, record['target']
                            archive.addfile(item)
                        else:
                            item.mode, item.size = record['mode'], record['size']
                            with (root / name).open('rb') as data:
                                archive.addfile(item, data)
            stream.flush()
            os.fsync(stream.fileno())
        verify_archive(temporary)
        temporary.chmod(0o644)
        os.link(temporary, output)
        with checksum.open('x') as stream:
            stream.write(sha256(output) + '  ' + output.name + '\n')
        return output
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def extract_bundle(archive_path, destination, *, expected_sha256=None):
    """Verify before extracting; write only to a new directory, with symlinks last."""
    archive_path, destination = Path(archive_path), Path(destination).expanduser()
    if destination.is_symlink() or destination.exists():
        raise FileExistsError('Runtime extraction destination must be new.')
    destination = destination.resolve()
    if expected_sha256 is not None and not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
        raise ValueError('Runtime archive checksum mismatch.')
    # Pin one open file description from checksum verification through extraction.
    # Replacing the path after verification cannot substitute a different archive.
    with archive_path.open('rb') as raw:
        initial_digest = hashlib.file_digest(raw, 'sha256').hexdigest()
        if expected_sha256 is not None and initial_digest != expected_sha256:
            raise ValueError('Runtime archive checksum mismatch.')
        raw.seek(0)
        created = False
        try:
            with tarfile.open(fileobj=raw, mode='r:*') as archive:
                manifest = _verify_open_archive(archive)
                root_name, entries = _tar_entries(archive)
                destination.mkdir(parents=True, mode=0o700)
                created = True
                for name, member in sorted(entries.items()):
                    target = destination.joinpath(*safe_relative(name).parts)
                    if member.issym():
                        continue
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.extractfile(member) as source, target.open('xb') as output:
                            shutil.copyfileobj(source, output)
                    target.chmod(member.mode)
                for name, member in sorted(entries.items()):
                    if member.issym():
                        destination.joinpath(*safe_relative(name).parts).symlink_to(member.linkname)
            raw.seek(0)
            if hashlib.file_digest(raw, 'sha256').hexdigest() != initial_digest:
                raise ValueError('Runtime archive changed while it was being extracted.')
            verify_bundle(destination)
            return manifest
        except BaseException:
            if created:
                shutil.rmtree(destination)
            raise
