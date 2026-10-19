#!/usr/bin/env python3
# Code style: AI-code. AI-generated coherent streaming tools archive preparation.
"""Patch a new tools archive and seal both its file and TEA runtime manifests.

The original archive is never changed. Explicit replacements are recorded;
compiled analysis code, detector configuration and runtime pins are preserved.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def read_archive(path):
    files, modes = {}, {}
    with tarfile.open(path, 'r:gz') as source:
        for member in source.getmembers():
            name = member.name
            if (PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
                    or not (member.isfile() or member.isdir())):
                raise ValueError('Unsafe tools archive member: ' + name)
            if member.isdir():
                continue
            if name in files:
                raise ValueError('Duplicate tools archive member: ' + name)
            files[name] = source.extractfile(member).read()
            modes[name] = member.mode
    return files, modes


def validate(files):
    outer = json.loads(files['source_manifest.json'])
    if set(outer) != set(files) - {'source_manifest.json'}:
        raise ValueError('Outer manifest does not cover every tools file')
    for name, expected in outer.items():
        if digest(files[name]) != expected:
            raise ValueError('Outer tools checksum differs: ' + name)
    inner = json.loads(files['tea/source_manifest.json'])
    expected = {name[4:] for name in files if name.startswith('tea/')
                and name != 'tea/source_manifest.json'}
    if set(inner) - {'relocation_prefixes'} != expected:
        raise ValueError('TEA manifest does not cover every runtime overlay file')
    for name, row in inner.items():
        if name == 'relocation_prefixes':
            continue
        data = files['tea/' + name]
        if row['sha256'] != digest(data) or row['bytes'] != len(data):
            raise ValueError('Frozen analysis payload changed: ' + name)
    for name in files:
        if name.startswith('tea/configs/'):
            duplicate = 'tea/bin/' + PurePosixPath(name).name
            if duplicate in files and files[duplicate] != files[name]:
                raise ValueError('Duplicate runtime config differs: ' + name)


def prepare(source, destination, replacements):
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise ValueError('Use a new tools archive path')
    files, modes = read_archive(source)
    old_inner = json.loads(files['tea/source_manifest.json'])
    if 'relocation_prefixes' not in old_inner:
        raise ValueError('Missing frozen runtime relocation authority')
    changed = {}
    for name, path in replacements.items():
        if (PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
                or name in ('source_manifest.json', 'tea/source_manifest.json', 'tools_patch_manifest.json')
                or not name.startswith(('tea/utils/', 'tea/configs/'))):
            raise ValueError('Only explicit analysis helper/config replacements are supported')
        payload = Path(path).read_bytes()
        files[name] = payload
        modes[name] = modes.get(name, 0o644)
        changed[name] = dict(source=str(Path(path).resolve()), sha256=digest(payload))
    # Python entry points in bin/ search their own directory first. Keep its
    # existing config copies equal to configs/, even when PYTHONPATH is set.
    mirrored = []
    for name in list(files):
        if name.startswith('tea/configs/'):
            duplicate = 'tea/bin/' + PurePosixPath(name).name
            if duplicate in files and files[duplicate] != files[name]:
                files[duplicate] = files[name]
                mirrored.append(duplicate)
    inner = {'relocation_prefixes': old_inner['relocation_prefixes']}
    for name in sorted(files):
        if name.startswith('tea/') and name != 'tea/source_manifest.json':
            relative = name[4:]
            row = dict(old_inner.get(relative, {}))
            row.update(sha256=digest(files[name]), bytes=len(files[name]))
            if name in changed:
                row['source'] = changed[name]['source']
            elif name in mirrored:
                row['source'] = 'frozen-overlay:configs/' + PurePosixPath(name).name
            inner[relative] = row
    files['tea/source_manifest.json'] = json_bytes(inner)
    files['tools_patch_manifest.json'] = json_bytes(dict(
        schema='shift-stream-tools-patch-v1', base_archive_sha256=digest(source.read_bytes()),
        explicit_replacements=changed, mirrored_config_copies=mirrored,
        detector_and_runtime_inputs_changed=False))
    modes['tools_patch_manifest.json'] = 0o644
    files['source_manifest.json'] = json_bytes({name: digest(data) for name, data in files.items()
                                               if name != 'source_manifest.json'})
    validate(files)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open('xb') as output:
            with tarfile.open(fileobj=output, mode='w:gz') as archive:
                for name in sorted(files):
                    member = tarfile.TarInfo(name)
                    member.size, member.mode = len(files[name]), modes[name]
                    archive.addfile(member, io.BytesIO(files[name]))
        validate(read_archive(destination)[0])
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return dict(path=str(destination.resolve()), sha256=digest(destination.read_bytes()),
                bytes=destination.stat().st_size, files=len(files),
                explicit_replacements=changed, mirrored_config_copies=mirrored)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--replacements', type=Path, required=True,
                        help='JSON mapping archive paths to explicit local source files')
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.destination,
                             json.loads(args.replacements.read_text())), indent=2))


if __name__ == '__main__':
    main()
