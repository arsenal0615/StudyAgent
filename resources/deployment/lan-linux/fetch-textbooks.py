#!/usr/bin/env python3
"""Download the ChinaTextbook PDFs registered by StudyAgent and verify each SHA-256.

The 307 originals referenced by resources/curriculum/chinatextbook-{chinese,primary}-v1.json
come from the public TapXWorld/ChinaTextbook repository. Every file is accepted only
when its size and SHA-256 equal the manifest; nothing else is written.

Usage: python3 fetch-textbooks.py <repo checkout> /data/studyagent/ChinaTextbook
"""
import concurrent.futures as futures
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import urllib.parse
import urllib.request

BASES = ('https://raw.githubusercontent.com/TapXWorld/ChinaTextbook/master/',
         'https://media.githubusercontent.com/media/TapXWorld/ChinaTextbook/master/')


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def rows(repository: Path):
    seen = {}
    for name in ('chinatextbook-chinese-v1.json', 'chinatextbook-primary-v1.json'):
        for book in json.loads((repository / 'resources/curriculum' / name).read_text('utf-8'))['books']:
            seen[book['repositoryPath']] = (book['sha256'], int(book['bytes']))
    return sorted(seen.items())


def fetch(root: Path, item):
    relative, (digest, size) = item
    parts = PurePosixPath(relative).parts
    if PurePosixPath(relative).is_absolute() or '..' in parts:
        raise ValueError('unsafe path ' + relative)
    destination = root.joinpath(*parts)
    if destination.is_file() and destination.stat().st_size == size and sha256(destination) == digest:
        return 'kept', relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.part')
    for _attempt in range(3):
        for base in BASES:
            try:
                with urllib.request.urlopen(base + urllib.parse.quote(relative), timeout=300) as response, partial.open('wb') as output:
                    for block in iter(lambda: response.read(1 << 20), b''):
                        output.write(block)
                if partial.stat().st_size == size and sha256(partial) == digest:
                    os.replace(partial, destination)
                    return 'downloaded', relative
            except OSError:
                pass
    partial.unlink(missing_ok=True)
    return 'FAILED', relative


def main() -> int:
    repository, root = Path(sys.argv[1]), Path(sys.argv[2])
    counts = {}
    with futures.ThreadPoolExecutor(6) as pool:
        for status, relative in pool.map(lambda item: fetch(root, item), rows(repository)):
            counts[status] = counts.get(status, 0) + 1
            if status == 'FAILED':
                print('FAILED', relative, flush=True)
    print(json.dumps(counts))
    return 1 if counts.get('FAILED') else 0


if __name__ == '__main__':
    raise SystemExit(main())
