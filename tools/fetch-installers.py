#!/usr/bin/env python3
"""Fetch pinned vendor installers to a private local cache; never execute them."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 2 * 1024 ** 3
ALLOWED_HOSTS = {'updates.quantower.com', 'builds.dotnet.microsoft.com'}


def checked_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS or
            parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('Installer URL is outside the official HTTPS allowlist.')
    return url


class OfficialRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        checked_url(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


def open_official(request, timeout):
    return urllib.request.build_opener(OfficialRedirectHandler()).open(request, timeout=timeout)


def digest(path, algorithm):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def fetch(item, cache, opener=open_official):
    cache = Path(cache).expanduser().absolute()
    if cache.is_symlink():
        raise ValueError('Cache directory must not be a symlink.')
    cache.mkdir(parents=True, mode=0o700, exist_ok=True)
    if cache.stat().st_uid != os.getuid():
        raise ValueError('Cache directory is owned by another user.')
    os.chmod(cache, 0o700)
    name = item['filename']
    if not name or Path(name).name != name or name in ('.', '..'):
        raise ValueError('Installer filename must be a basename.')
    algorithm = item['algorithm']
    if algorithm not in ('sha256', 'sha512'):
        raise ValueError('Unsupported digest algorithm.')
    expected = item['digest']
    if len(expected) != hashlib.new(algorithm).digest_size * 2 or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('Malformed pinned digest.')
    url = checked_url(item['url'])
    destination = cache / name
    if destination.is_symlink():
        raise ValueError('Cached installer must not be a symlink.')
    if destination.exists():
        if not destination.is_file() or digest(destination, algorithm) != expected:
            raise ValueError('Existing cached installer does not match its pin; it was not changed.')
        return destination
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache, prefix='.download-', delete=False) as stream:
            temporary = Path(stream.name)
            request = urllib.request.Request(url, headers={'User-Agent': 'quantower-linux-toolkit/0.1'})
            with opener(request, timeout=60) as response:
                checked_url(response.geturl())
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError('Installer exceeds the bounded download size.')
                    stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if digest(temporary, algorithm) != expected:
            raise ValueError('Downloaded installer checksum mismatch; nothing was installed.')
        os.chmod(temporary, 0o600)
        # Atomic no-clobber publication even if another downloader raced us.
        os.link(temporary, destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', required=True, type=Path)
    parser.add_argument('--only', choices=('amp', 'desktop', 'all'), default='all')
    args = parser.parse_args(argv)
    os.umask(0o077)
    lock = json.loads((ROOT / 'installer-lock.json').read_text())
    if lock.get('schema_version') != 1:
        parser.error('Unsupported installer lock schema.')
    result = []
    for name, item in lock['artifacts'].items():
        if args.only not in ('all', name):
            continue
        path = fetch(item, args.cache)
        result.append({'artifact': name, 'path': str(path), 'sha256': digest(path, 'sha256'),
                       'pinned_digest_verified': True, 'executed': False})
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
