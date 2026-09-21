#!/usr/bin/env python3
"""Independent four-stage identity audit for completed full and replay pilots."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('summary',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--report-path-containing', help='Audit only report paths containing this literal text')
    args=p.parse_args()
    import ROOT
    from DataFormats.FWLite import Events
    summary=json.loads(args.summary.read_text())
    groups=summary['groups']
    if isinstance(groups,dict):groups=list(groups.values())
    reports=[Path(path) for g in groups for path in g['reports']]
    if args.report_path_containing:
        reports=[path for path in reports if args.report_path_containing in str(path)]
    if not reports:
        raise ValueError('No completed reports to audit')
    result=dict(status='running',requested_reports=len(reports),rows=[],reconstructed_mass_read=False,
                report_path_containing=args.report_path_containing)
    names=('step1/events_step1_part{part}.root','step2/events_step2_part{part}.root',
           'step3/events_AOD_part{part}.root','step4/events_NanoAOD_part_{part}.root')
    try:
        with tempfile.TemporaryDirectory(prefix='shift_stage_identity_') as tmp:
            local=Path(tmp)/'input.root'
            for report_path in reports:
                report=json.loads(report_path.read_text())
                if report['status']!='validated' or report['reconstructed_mass_read']:
                    raise ValueError('Require validated mass-free report')
                expected=[tuple(row['id']) for row in report.get('rows',report.get('generation',{}).get('rows',[]))]
                if not expected or len(set(expected))!=len(expected):
                    raise ValueError('Invalid reference identities')
                part=report_path.parent.name.removeprefix('part')
                replay=report_path.parent.parent.name=='sampling_replay'
                campaign=report_path.parents[2 if replay else 3]
                row=dict(report=str(report_path),events=len(expected),stages={})
                for stage,name in enumerate(names,1):
                    source=campaign/'samples'/name.format(part=part)
                    shutil.copy2(source,local)
                    f=ROOT.TFile.Open(str(local))
                    if not f or f.IsZombie() or f.TestBit(ROOT.TFile.kRecovered):
                        raise ValueError(f'Invalid ROOT: {source}')
                    tree=f.Get('Events')
                    if not tree or tree.GetEntries()!=len(expected):
                        raise ValueError(f'Invalid count: {source}')
                    ids=[]
                    if stage==4:
                        tree.SetBranchStatus('*',0)
                        for b in ('run','luminosityBlock','event'):tree.SetBranchStatus(b,1)
                        for e in tree:ids.append((int(e.run),int(e.luminosityBlock),int(e.event)))
                        f.Close()
                    else:
                        f.Close()
                        for e in Events(str(local)):
                            a=e.eventAuxiliary();ids.append((int(a.run()),int(a.luminosityBlock()),int(a.event())))
                    if ids!=expected:raise ValueError(f'Identity mismatch: {source}')
                    row['stages'][str(stage)]=dict(events=len(ids),bytes=source.stat().st_size)
                result['rows'].append(row)
                if len(result['rows'])%10==0:print('Validated',len(result['rows']),'four-stage chunks',flush=True)
        result['status']='validated'
    except Exception as error:
        result['status']='failed';result['error']=repr(error);raise
    finally:
        args.output.write_text(json.dumps(result,indent=2)+'\n')
        print(result['status'],len(result['rows']),'of',len(reports))


if __name__=='__main__':main()
