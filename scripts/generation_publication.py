#!/usr/bin/env python3
"""Bind audited GEN metadata to the published ROOT file by SHA-256."""
import argparse
import hashlib
import json
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def finalize(metadata, published):
    record = json.loads(Path(metadata).read_text())
    expected = record.get('input_sha256')
    if not expected or record.get('schema') != 'shift-production-gen-v1':
        raise ValueError('Missing audited input checksum or generation schema')
    actual = sha256(published)
    if actual != expected:
        raise ValueError('Published ROOT differs from the audited GEN input')
    record['audit_input'] = record['input']
    record['input'] = str(Path(published).resolve())
    record['published_output_sha256'] = actual
    record['publication_complete'] = True
    return record


def verify(record, published):
    if (record.get('publication_complete') is not True or
            record.get('input') != str(Path(published).resolve()) or
            not record.get('published_output_sha256') or
            record['published_output_sha256'] != record.get('input_sha256') or
            sha256(published) != record['published_output_sha256']):
        raise ValueError('GEN output and audited metadata are not a validated pair')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('metadata', type=Path)
    parser.add_argument('published', type=Path)
    parser.add_argument('--output', type=Path,
                        help='Write finalized metadata; without it, verify an existing pair')
    args = parser.parse_args()
    if args.output:
        record = finalize(args.metadata, args.published)
        with args.output.open('x') as stream:
            stream.write(json.dumps(record, indent=2) + '\n')
    else:
        verify(json.loads(args.metadata.read_text()), args.published)
    print('Validated GEN publication pair:', args.published)


if __name__ == '__main__':
    main()
