# Code style: AI-code. AI-generated selected-signal publication accounting.
"""Keep detector admission separate from the unchanged full signal GEN parent."""
import copy
import hashlib
import json
import math
from pathlib import Path

from shift_production_selection import validate_contract


WEIGHT_BRANCH = 'shiftSamplingGenWeight'
SAMPLING_BRANCHES = ('shiftSamplingProbability', 'shiftSamplingWeight',
                     WEIGHT_BRANCH, 'shiftOriginalGenWeight')


def population(row, parent):
    """Return ordered admitted IDs and their exact inclusion probabilities."""
    full = parent['event_ids']
    if parent.get('events', len(full)) != len(full) or len(set(map(tuple, full))) != len(full):
        raise ValueError('Incomplete or repeated full GEN parent identity')
    selection = row.get('material_selection')
    if selection is None:
        if row['events'] != len(full):
            raise ValueError('Unselected signal count differs from its full GEN parent')
        return full, [1.] * len(full)
    validate_contract(selection['production_selection'])
    if (selection['gen_sha256'] != parent['gen_sha256']
            or selection['full_gen_events'] != len(full)):
        raise ValueError('Material admission changed its full GEN parent')
    indices = selection['selected_indices']
    probabilities = selection['sampling_probabilities']
    if (len(indices) != row['events'] or len(probabilities) != len(indices)
            or indices != sorted(set(indices))
            or any(type(i) is not int or not 0 <= i < len(full) for i in indices)
            or any(type(p) not in (int,float) or not math.isfinite(p) or not 0 < p <= 1
                   for p in probabilities)):
        raise ValueError('Invalid ordered material admission population')
    return [full[i] for i in indices], probabilities


def bind(row, parent, descriptor):
    """Freeze an already prepared shared-policy draw without redrawing it."""
    if (descriptor['gen_sha256'] != parent['gen_sha256']
            or descriptor['event_ids'] != parent['event_ids']
            or descriptor.get('weights') != [1.] * parent['events']):
        raise ValueError('Material descriptor differs from audited unit-weight signal GEN')
    result = copy.deepcopy(row)
    result['full_gen_events'] = parent['events']
    result['events'] = len(descriptor['selected_indices'])
    result['material_selection'] = dict(gen_sha256=parent['gen_sha256'],
        full_gen_events=parent['events'],
        selected_indices=copy.deepcopy(descriptor['selected_indices']),
        sampling_probabilities=copy.deepcopy(descriptor['sampling_probabilities']),
        production_selection=copy.deepcopy(descriptor['production_selection']))
    population(result,parent)
    return result


def chunk_spec(plan, inventory, point, chunk_id):
    row, = [r for r in plan['points'] if r['point'] == point]
    chunk, = [c for c in row['chunks'] if c['chunk'] == chunk_id]
    if (type(chunk_id) is not int or type(chunk['skip']) is not int
            or type(chunk['count']) is not int or chunk['skip'] < 0
            or not 1 <= chunk['count'] <= 20):
        raise ValueError('Invalid frozen selected-event slice')
    ids,_ = population(row,inventory['points'][point])
    expected = ids[chunk['skip']:chunk['skip']+chunk['count']]
    if len(expected) != chunk['count']:
        raise ValueError('Incomplete selected-event slice')
    return row,chunk,expected


def validate_nano(path, identities, probabilities):
    """Check all four Double W/p fields after transport, grouping and scoring."""
    import numpy as np
    import uproot
    with uproot.open(path,handler=uproot.source.file.MemmapSource) as source:
        arrays=source['Events'].arrays(['run','luminosityBlock','event','genWeight',
                                       *SAMPLING_BRANCHES],library='np')
    actual=[[int(r),int(l),int(e)] for r,l,e in zip(arrays['run'],arrays['luminosityBlock'],arrays['event'])]
    if actual != identities or not np.array_equal(arrays['genWeight'],np.ones(len(identities))):
        raise ValueError('Material publication changed selected identities or native GEN weights')
    p=np.asarray(probabilities,dtype=np.float64)
    expected=(p,1./p,1./p,np.ones(len(p),dtype=np.float64))
    for name,values in zip(SAMPLING_BRANCHES,expected):
        if arrays[name].dtype != np.dtype('float64') or not np.array_equal(arrays[name],values):
            raise ValueError('Material publication changed a Double sampling field: '+name)
    return dict(events=len(p),sumw=math.fsum(map(float,1./p)),
                sumw2=math.fsum(float(w)*float(w) for w in 1./p),
                weight_branch=WEIGHT_BRANCH,full_gen_normalization_preserved=True)


def validate_plan(plan,inventory):
    """Validate selected offsets and bind every selected descriptor into its row."""
    for row in plan['points']:
        parent=inventory['points'][row['point']]
        path=Path(row['gen_directory'])/'material_selected_source.json'
        if not path.is_file():
            raise ValueError('New BSM production needs prepared material_selected_source.json')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if row.get('material_selected_source_sha256') != digest:
            raise ValueError('Material descriptor differs from its pinned checksum')
        descriptor=json.loads(path.read_text())
        expected=bind(row,parent,descriptor)
        if row.get('material_selection') != expected['material_selection'] or row['events'] != expected['events']:
            raise ValueError('Detector planning must use the frozen selected-event population')


def merged_cutflow_sumw(paths):
    """Follow ROOT's stored bin precision when summing already-filled cutflows."""
    import numpy as np
    import uproot
    total = None
    for path in paths:
        with uproot.open(path) as source:
            histogram = source['cutFlow']
            cast = np.float32 if histogram.classname == 'TH1F' else np.float64
            value = histogram.values(flow=True)[1]
        total = cast(value) if total is None else cast(total + value)
    if total is None:
        raise ValueError('Cannot merge an empty histogram population')
    return float(total)


def rate_only_report(row, parent, plan_sha256, seal_sha256):
    """Close an empty admission draw without inventing detector events or a rate."""
    ids, probabilities = population(row,parent)
    if ids or probabilities or row.get('material_selection') is None or parent['events'] < 1:
        raise ValueError('A rate-only cohort requires an empty draw from positive full GEN exposure')
    return dict(schema='shift-dark-photon-rate-only-complete-v1',complete=True,rate_only=True,
        point=row['point'],events=0,full_gen_events=parent['events'],
        material_selection=copy.deepcopy(row['material_selection']),weight_branch=WEIGHT_BRANCH,
        histogram_sumw=0.,standard_cutflow_sumw=0.,plan_sha256=plan_sha256,seal_sha256=seal_sha256,
        final_output_base=row['final_output_base'],
        union=dict(complete=True,parent_gen=copy.deepcopy(parent),parent_gen_ledger_count=1,
            event_ids=[],events=0,native_sumw=0.,native_sumw2=0.,groups=[],inputs=[]))


def validate_rate_only_report(report, row, parent, plan_sha256, seal_sha256):
    expected=rate_only_report(row,parent,plan_sha256,seal_sha256)
    if any(report.get(key) != value for key,value in expected.items()):
        raise ValueError('Rate-only completion changed its full GEN exposure or empty draw')
    if report.get('score_histograms') or report.get('groups'):
        raise ValueError('A rate-only cohort cannot claim detector histograms or Nano groups')
