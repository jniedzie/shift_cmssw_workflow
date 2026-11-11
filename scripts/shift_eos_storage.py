#!/usr/bin/env python3
# Code style: AI-code.
"""Select the EOS instance from an explicit, traversal-free storage path."""
from pathlib import PurePosixPath
from urllib.parse import urlsplit

GROUP_ROOT = '/eos/cms/store/group/phys_exotica/xtracks/jeremi/shift_cmssw'
SUPPORT_ROOTS = ('/eos/user/j/jniedzie/shift_cmssw_support', GROUP_ROOT+'/support')


def eos_path(value):
    value = str(value)
    if value.startswith('root://'):
        parsed = urlsplit(value)
        if parsed.query or parsed.fragment:
            raise ValueError('EOS storage references must not contain URL options')
        value = '/'+parsed.path.lstrip('/')
    if not value.startswith('/eos/') or '..' in PurePosixPath(value).parts:
        raise ValueError('Require an absolute EOS path without traversal')
    return value


def eos_endpoint(value):
    path = eos_path(value)
    if path.startswith('/eos/user/') or path.startswith('/eos/home-'):
        return 'root://eosuser.cern.ch'
    if path == GROUP_ROOT or path.startswith(GROUP_ROOT+'/'):
        return 'root://eoscms.cern.ch'
    raise ValueError('EOS location is outside the qualified storage roots: '+path)


def xrootd_url(value):
    path = eos_path(value)
    return eos_endpoint(path)+'/'+path


def is_support_path(value):
    try:
        path = eos_path(value)
    except ValueError:
        return False
    return any(path == root or path.startswith(root+'/') for root in SUPPORT_ROOTS)


def detector_source(request):
    """Read a legacy inline descriptor or its exact staged, hash-pinned copy."""
    if 'detector_source' in request:
        return request['detector_source']
    import hashlib
    import json
    from pathlib import Path
    reference = request['detector_source_ref']
    canonical = Path(reference['path'])
    if (not canonical.is_absolute() or '..' in canonical.parts
            or not str(canonical).startswith('/afs/cern.ch/work/j/jniedzie/private/shift_cmssw/')):
        raise ValueError('Invalid persistent source-descriptor reference')
    staged = Path(canonical.name)
    source = staged if staged.is_file() else canonical
    if source.stat().st_size > 4*1024*1024:
        raise ValueError('Source descriptor exceeds its bounded size')
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != reference['sha256']:
        raise ValueError('Frozen source descriptor changed')
    encoding = reference.get('encoding', 'json')
    if encoding == 'gzip-source-authority-v1':
        import gzip
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            data = stream.read(16*1024*1024+1)
        if len(data) > 16*1024*1024:
            raise ValueError('Expanded source authority exceeds its bounded size')
        authority = json.loads(data)
        if (authority.get('complete') is not True
                or authority.get('primary_preflight', {}).get('complete') is not True):
            raise ValueError('Source authority lacks successful full-census preflight')
        return authority['selected_source']
    if encoding != 'json':
        raise ValueError('Unknown source descriptor encoding')
    return json.loads(data)
