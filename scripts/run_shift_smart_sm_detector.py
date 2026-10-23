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
        environment = dict(os.environ)
        for name in ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'PYTHONHOME'):
            environment.pop(name, None)
        environment['PATH'] = '/usr/bin:/bin'
        text = subprocess.run(['/usr/bin/eos', '-b', 'root://eoshome-j.cern.ch',
            'quota', 'ls', '-m', '/eos/user/j/jniedzie'], check=True, timeout=60,
            capture_output=True, text=True, env=environment).stdout
        rows = [dict(re.findall(r'(\w+)=([^\s]+)', line)) for line in text.splitlines()]
        row = next(row for row in rows if row.get('space') == '/eos/user/j/jniedzie/')
        Path('report.json').write_text(json.dumps(dict(complete=True, checked_epoch=time.time(),
            free_bytes=int(row['maxlogicalbytes'])-int(row['usedlogicalbytes']),
            free_files=int(row['maxfiles'])-int(row['usedfiles'])))+'\n')
        return
    source = request['detector_source']
    # Source paths are already the audited immutable canonical EOS paths.
    source = dict(source, gen_path_resolved=True)
    Path('selected_source.json').write_text(json.dumps(source, indent=2)+'\n')
    tools = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(tools/'scripts/run_shift_gen_to_nano.py'),
        'selected_source.json', '--skip', str(request['selected_skip']),
        '--count', str(request['selected_count']), '--job', str(request['detector_job']),
        '--templates', str(tools/'templates'), '--publish', '--remove-consumed-intermediates',
        '--absolute-stage-timeouts', '--stage-timeouts', '18000,3600,5400,10800',
        '--first-event-stall-timeout', '7200', '--event-stall-timeout', '3600'],
        check=True, timeout=43200)


if __name__ == '__main__':
    main(sys.argv[1])
