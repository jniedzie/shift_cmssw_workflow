#!/usr/bin/env python3
# Code style: AI-code. AI-generated immutable SM detector request entry.
"""Run the common weighted detector chain, or report authenticated EOS quota."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def main(path):
    request = json.loads(Path(path).read_text())
    if request['stage'] == 'smart_sm_inventory':
        from run_shift_weighted_gen import publish
        Path('source_inventory.json').write_text(json.dumps(request['inventory'], indent=2)+'\n')
        publications = publish([(Path('source_inventory.json'), 'source_inventory.json')],
                               request['publication_directory'])
        Path('report.json').write_text(json.dumps(dict(complete=True, published=publications,
            published_storage_bytes=sum(row['bytes'] for row in publications)))+'\n')
        return
    if request['stage'] == 'smart_sm_quota':
        from shift_eos_storage import eos_endpoint, eos_path
        environment = dict(os.environ)
        for name in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'PYTHONHOME'):
            environment.pop(name, None)
        environment['PATH'] = '/usr/bin:/bin'
        peers=None
        if request.get('peer_storage_authority'):
            from shift_sm_peer_storage import read_peer_commitments
            peers=read_peer_commitments(request['peer_storage_authority'])
        quota_path = eos_path(request.get('quota_path', '/eos/user/j/jniedzie'))
        endpoint = ('root://eoshome-j.cern.ch' if quota_path == '/eos/user/j/jniedzie'
                    else eos_endpoint(quota_path))
        text = subprocess.run(['/usr/bin/eos', '-b', endpoint,
            'quota', 'ls', '-m', quota_path], check=True, timeout=60,
            capture_output=True, text=True, env=environment).stdout
        rows = [dict(re.findall(r'(\w+)=([^\s]+)', line)) for line in text.splitlines()]
        row = next(row for row in rows if (row.get('space') == '/eos/user/j/jniedzie/'
            if endpoint == 'root://eoshome-j.cern.ch' else
            row.get('gid') == 'zh' and quota_path.startswith(row.get('space', '\0'))))
        Path('report.json').write_text(json.dumps(dict(complete=True, checked_epoch=time.time(),
            quota_path=quota_path, quota_endpoint=endpoint,
            free_bytes=int(row['maxlogicalbytes'])-int(row['usedlogicalbytes']),
            free_files=int(row['maxfiles'])-int(row['usedfiles']),
            **(dict(peer_storage=peers) if peers is not None else {})))+'\n')
        return
    from shift_eos_storage import detector_source
    source = detector_source(request)
    # Source paths are already the audited immutable canonical EOS paths.
    source = dict(source, gen_path_resolved=True)
    Path('selected_source.json').write_text(json.dumps(source, indent=2)+'\n')
    tools = Path(__file__).resolve().parents[1]
    seed_arguments = ['--seed', str(request['detector_seed'])] if 'detector_seed' in request else []
    subprocess.run([sys.executable, str(tools/'scripts/run_shift_gen_to_nano.py'),
        'selected_source.json', '--skip', str(request['selected_skip']),
        '--count', str(request['selected_count']), '--job', str(request['detector_job']),
        '--templates', str(tools/'templates'), '--publish', '--remove-consumed-intermediates',
        '--absolute-stage-timeouts', '--stage-timeouts', '18000,3600,5400,10800',
        '--first-event-stall-timeout', '7200', '--event-stall-timeout', '3600', *seed_arguments],
        check=True, timeout=43200)


if __name__ == '__main__':
    main(sys.argv[1])
