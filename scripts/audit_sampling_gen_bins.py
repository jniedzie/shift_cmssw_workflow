#!/usr/bin/env python3
"""Independently audit Born bin ownership of completed GEN sampling pilots."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import tempfile
from pythia_pthat import from_hepmc


def main():
    from DataFormats.FWLite import Events, Handle
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('summary', type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    summary = json.loads(args.summary.read_text())
    if not summary['complete']:
        raise ValueError('Require complete explicit GEN manifest')
    result = {}
    with tempfile.TemporaryDirectory(prefix='shift_born_audit_') as temporary:
        local = Path(temporary)/'gen.root'
        for key, group in summary['groups'].items():
            for report_path in group['reports']:
                path = Path(report_path)
                r = json.loads(path.read_text())
                c = r['contract']
                if c['mode'] != 'gen':
                    raise ValueError('GEN-only audit')
                shutil.copyfile(path.with_name('gen.root'), local)
                values, ids, excursions = [], [], Counter()
                for event in Events(str(local)):
                    info, hep = Handle('GenEventInfoProduct'), Handle('edm::HepMCProduct')
                    event.getByLabel('generator', info)
                    event.getByLabel(('generator', 'unsmeared'), hep)
                    if not info.isValid() or not hep.isValid():
                        raise ValueError('Missing GEN product')
                    code, stored = int(info.product().signalProcessID()), float(info.product().binningValues()[0])
                    born = from_hepmc(hep.product().GetEvent(), code, stored)
                    if born < c['lower']-1.e-8 or (c['upper'] > 0 and born > c['upper']+1.e-8):
                        raise ValueError(f'Born bin violation in {key}: {born}')
                    if stored < c['lower'] or (c['upper'] > 0 and stored > c['upper']):
                        excursions[str(code)] += 1
                    values.append(born)
                    a = event.eventAuxiliary()
                    ids.append([int(a.run()), int(a.luminosityBlock()), int(a.event())])
                if ids != [row['id'] for row in r['generation']['rows']]:
                    raise ValueError('GEN identities differ from saved report')
                result[key] = dict(events=len(values), born_range=[min(values), max(values)],
                                   stored_excursions_by_code=dict(excursions), violations=0)
                print(key, result[key], flush=True)
    args.output.write_text(json.dumps(dict(status='validated', bins=result,
        physics_valid=False, normalization_ready=False), indent=2)+'\n')


if __name__ == '__main__':
    main()
