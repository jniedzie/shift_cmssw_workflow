#!/usr/bin/env python3
# Code style: AI-code.
"""Pack a restricted ROOT file against a pinned reference without opening its branches."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import tempfile
import time


CODEC = 'zstd-patch-v1'
ROOT_LIMIT = 4 * 1024**2
ENCODED_LIMIT = 256 * 1024
ZSTD = '/usr/bin/zstd'


def _fingerprint(data):
    return dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def _validate_pin(pin, limit, label):
    if (not isinstance(pin, dict) or set(pin) != {'bytes', 'sha256'}
            or type(pin['bytes']) is not int or not 0 < pin['bytes'] <= limit
            or not isinstance(pin['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', pin['sha256'])):
        raise ValueError(f'Invalid {label} byte count or SHA-256')


def _check_file(path, pin, limit, label):
    _validate_pin(pin, limit, label)
    if not path.is_file() or path.is_symlink():
        raise ValueError(f'The {label} must be a regular file')
    with path.open('rb') as stream:
        observed = _fingerprint(stream.read(limit + 1))
    if observed != pin:
        raise ValueError(f'The {label} differs from its pinned bytes')


def _zstd(source, reference, limit, *, decompress=False):
    arguments = [ZSTD, '--quiet', '--stdout', '-M64M', f'--patch-from={reference.resolve()}']
    if decompress:
        arguments.append('--decompress')
    arguments.extend(['--', str(source.resolve())])
    deadline = time.monotonic() + 60
    output = bytearray()
    with subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError('The ROOT delta codec timed out')
                    block = os.read(process.stdout.fileno(), min(65536, limit + 1 - len(output)))
                    if not block:
                        break
                    output.extend(block)
                    if len(output) > limit:
                        raise ValueError('The ROOT delta codec exceeded its output byte limit')
            if process.wait(timeout=max(0, deadline - time.monotonic())) != 0 or not output:
                raise ValueError('The ROOT delta codec failed')
        finally:
            if process.poll() is None:
                process.kill()
    return output


def encode(root, reference, target, *, reference_pin, root_pin):
    """Create a packed file once and return its reference and original-byte receipt."""
    root, reference, target = map(Path, (root, reference, target))
    _check_file(reference, reference_pin, ROOT_LIMIT, 'reference ROOT')
    _check_file(root, root_pin, ROOT_LIMIT, 'original ROOT')
    packed = _zstd(root, reference, ENCODED_LIMIT)
    with tempfile.NamedTemporaryFile(prefix='.root-delta-', dir=target.parent) as pending:
        pending.write(packed)
        pending.flush()
        decoded = _zstd(Path(pending.name), reference, root_pin['bytes'], decompress=True)
        if _fingerprint(decoded) != root_pin:
            raise ValueError('The packed ROOT does not reconstruct its original bytes')
        _check_file(reference, reference_pin, ROOT_LIMIT, 'reference ROOT')
        os.link(pending.name, target)
    return dict(codec=CODEC, reference=dict(reference_pin), encoded=_fingerprint(packed), root=dict(root_pin))


def decode(encoded, reference, target, receipt):
    """Verify the packed file and reference, then materialize the original bytes once."""
    encoded, reference, target = map(Path, (encoded, reference, target))
    if (not isinstance(receipt, dict) or set(receipt) != {'codec', 'reference', 'encoded', 'root'}
            or receipt['codec'] != CODEC):
        raise ValueError('Unknown ROOT delta receipt')
    _check_file(reference, receipt['reference'], ROOT_LIMIT, 'reference ROOT')
    _check_file(encoded, receipt['encoded'], ENCODED_LIMIT, 'packed ROOT')
    _validate_pin(receipt['root'], ROOT_LIMIT, 'original ROOT')
    decoded = _zstd(encoded, reference, receipt['root']['bytes'], decompress=True)
    if _fingerprint(decoded) != receipt['root']:
        raise ValueError('The decoded ROOT differs from its pinned original bytes')
    with tempfile.NamedTemporaryFile(prefix='.root-delta-', dir=target.parent) as pending:
        pending.write(decoded)
        pending.flush()
        os.link(pending.name, target)
    return dict(receipt['root'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    pack = commands.add_parser('encode')
    unpack = commands.add_parser('decode')
    for command in (pack, unpack):
        command.add_argument('source', type=Path)
        command.add_argument('reference', type=Path)
        command.add_argument('target', type=Path)
    for name in ('reference', 'root'):
        pack.add_argument(f'--{name}-bytes', type=int, required=True)
        pack.add_argument(f'--{name}-sha256', required=True)
    unpack.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'encode':
        receipt = encode(args.source, args.reference, args.target,
                         reference_pin=dict(bytes=args.reference_bytes, sha256=args.reference_sha256),
                         root_pin=dict(bytes=args.root_bytes, sha256=args.root_sha256))
    else:
        receipt = decode(args.source, args.reference, args.target, json.loads(args.receipt.read_text()))
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()
