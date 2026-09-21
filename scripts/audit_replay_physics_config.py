#!/usr/bin/env python3
"""Compare unchanged simulation physics modules across combined and split jobs."""
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys

NAMES=('g4SimHits','LHCTransport','generatorSmeared','GlobalTag',
       'shiftLssWorkflowContract','shiftLssGeometryESSource','shiftLssMagneticField')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline',type=Path)
    p.add_argument('replay',type=Path,nargs='?')
    p.add_argument('--inspect',action='store_true')
    p.add_argument('--output',type=Path)
    args=p.parse_args()
    if args.inspect:
        with contextlib.redirect_stdout(io.StringIO()):
            process=runpy.run_path(str(args.baseline))['process']
        hashes={name:hashlib.sha256(getattr(process,name).dumpPython().encode()).hexdigest() for name in NAMES}
        print(json.dumps(hashes));return
    if args.replay is None or args.output is None:p.error('Replay and output required')
    def inspect(path):return json.loads(subprocess.check_output([sys.executable,__file__,str(path),'--inspect'],text=True))
    baseline,replay=inspect(args.baseline),inspect(args.replay)
    if baseline!=replay:raise ValueError('Changed physics configuration: '+str([n for n in NAMES if baseline[n]!=replay[n]]))
    args.output.write_text(json.dumps(dict(status='validated',baseline=str(args.baseline),replay=str(args.replay),hashes=baseline),indent=2)+'\n')
    print('Validated identical physics modules:',', '.join(NAMES))


if __name__=='__main__':main()
