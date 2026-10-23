#!/usr/bin/env python3
# Code style: AI-code. AI-generated scratch-only direct-DY GEN runtime entry.
"""Generate in a pinned private runtime and retain full GEN accounting on EOS."""
import argparse
import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile



def run(directory, point):
    directory=Path(directory)
    spec=importlib.util.spec_from_file_location('_sealed_dp_gen_stage',directory/'gen_worker.py')
    stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)
    plan,config=stage.read(directory/'plan.json'),stage.read(directory/'config.json')
    row,=[r for r in plan['points'] if r['point']==point]
    target=Path(row['gen_directory'])
    if target.exists():
        raise ValueError('Private GEN must not overwrite a started source population')
    runtime=plan['private_gen_runtime']
    with tempfile.TemporaryDirectory(prefix='dp-private-GEN-',
            dir=os.environ.get('_CONDOR_SCRATCH_DIR','/tmp')) as temporary:
        scratch=Path(temporary);gen=scratch/'gen';archive=scratch/'runtime.tar.gz'
        subprocess.run(['/usr/bin/xrdcp','--silent','--cksum','adler32',runtime['url'],str(archive)],
                       check=True,timeout=900)
        argv=list(row['gen_argv']);argv[0]=sys.executable
        argv[argv.index('--output')+1]=str(gen)
        argv.extend(['--runtime-provenance',runtime['provenance']['path'],
                     '--runtime-archive',str(archive)])
        subprocess.run(argv,check=True,cwd=scratch,timeout=config['gen_worker_timeout_seconds'])
        working=dict(row,gen_directory=str(gen))
        publication,parent=stage.publish_gen(working,dict(config,scratch_base=str(scratch)))
        pending=target.with_name(target.name+'.next');pending.mkdir(parents=True)
        for source in gen.rglob('*'):
            if source.is_file() and source.suffix not in ('.root','.pyc') and '__pycache__' not in source.parts:
                destination=pending/source.relative_to(gen);destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,destination)
        proof=stage.read(pending/'root_publication.json');proof['local_path']=str(target/'gen.root')
        (pending/'root_publication.json').write_text(json.dumps(proof,indent=2)+'\n')
        pending.rename(target)
        return publication,parent


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--point',required=True)
    args=parser.parse_args();run(args.directory,args.point)


if __name__=='__main__':
    main()
