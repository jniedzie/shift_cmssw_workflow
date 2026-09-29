"""Shared source-model and Pythia-trial checks for SoftQCD/MPI partitions."""
import ast
import hashlib
import json
from pathlib import Path
import re

MODEL_CONTRACT = 'pythia8-softqcd-nd-cp5-processlevel3-v1'
PARTITION_CONTRACT = 'direct-hard-jpsi-status23or33-v1'
DIRECT_JPSI_IDS = {443, 9940003, 9941003, 9942003}
PARTITION_BINS = ((0., 1.), (1., 2.), (2., 5.), (5., 10.),
                  (10., 20.), (20., -1.))


def edm_run_offset(event_class, bounds):
    if event_class not in ('qcd', 'direct_jpsi') or tuple(bounds) not in PARTITION_BINS:
        raise ValueError('Unknown SoftQCD/MPI class or bin')
    return (6*int(event_class == 'direct_jpsi') + PARTITION_BINS.index(tuple(bounds)))*100000


_PROCESS_ROW = re.compile(
    r'^\s*\|\s*non-diffractive\s+101\s*\|\s*'
    r'(\d+)\s+(\d+)\s+(\d+)\s*\|\s*'
    r'([\d.eE+-]+)\s+([\d.eE+-]+)\s*\|', re.MULTILINE)


def process_statistics(log_path):
    """Read the final Pythia process-101 row; CMSSW prints it twice."""
    rows = _PROCESS_ROW.findall(Path(log_path).read_text(errors='replace'))
    if not rows:
        raise ValueError('Missing Pythia non-diffractive process-101 statistics')
    tried, selected, accepted, sigma_mb, error_mb = rows[-1]
    result = dict(tried=int(tried), selected=int(selected), accepted=int(accepted),
                  sigma_pb=float(sigma_mb)*1.e9, error_pb=float(error_mb)*1.e9)
    if not (result['tried'] >= result['selected'] >= result['accepted'] > 0
            and result['sigma_pb'] > 0 and result['error_pb'] >= 0):
        raise ValueError('Invalid Pythia non-diffractive trial statistics')
    return result


def model_settings_digest(fragment_path, event_class):
    """Hash the common model settings, excluding only the signal decay rule."""
    if event_class not in ('qcd', 'direct_jpsi'):
        raise ValueError('Unknown SoftQCD/MPI event class')
    source = Path(fragment_path).read_text()
    settings = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != 'vstring':
            continue
        values = [arg.value for arg in node.args
                  if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
        if 'SoftQCD:nonDiffractive = on' in values:
            settings = values
            break
    if not settings:
        raise ValueError('Missing literal SoftQCD/MPI process settings')
    required = {'SoftQCD:all = off', 'SoftQCD:nonDiffractive = on',
                'HardQCD:all = off', 'Charmonium:all = off',
                'Bottomonium:all = off', 'MultipartonInteractions:processLevel = 3'}
    if not required <= set(settings):
        raise ValueError('SoftQCD/MPI model settings changed or are incomplete')
    decay = [value for value in settings if value.startswith('443:')]
    if event_class == 'qcd' and decay:
        raise ValueError('QCD class changes the J/psi decay')
    if event_class == 'direct_jpsi' and set(decay) != {'443:onMode = off', '443:onIfMatch = 13 -13'}:
        raise ValueError('Unexpected direct-J/psi decay settings')
    if 'pythia8CP5SettingsBlock' not in source or 'ShiftMpiEventClassHook' not in source:
        raise ValueError('Missing CP5 or the SoftQCD/MPI event-class hook')
    model = [value for value in settings if not value.startswith('443:')]
    payload = json.dumps(model, separators=(',', ':')).encode()
    return hashlib.sha256(payload).hexdigest()
