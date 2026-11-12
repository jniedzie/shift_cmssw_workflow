# Code style: AI-code. AI-generated named DY source and rate prescription.
"""Keep hard-PDF settings, normalization inputs and their frozen proof together.

The events remain native LO plus parton shower. NNLO changes their physical
cross-section authority, never their GEN weights, exposure or decay branching.
The default recipe does not import or require these optional inputs.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re

PRESCRIPTION_NAMES = ('msht20-nnlo', 'msht20-nnlo-v2')
HELPER_NAME = 'dark_photon_dy_prescription.py'
TABLE_NAME = 'dy_msht20_nnlo_v1.json'
TABLE_NAMES = {'msht20-nnlo': TABLE_NAME, 'msht20-nnlo-v2': 'dy_msht20_nnlo_v2.json'}
SCHEMA = 'shift-dy-msht20-nnlo-v1'
TABLE_SCHEMA = 'shift-dy-msht20-nnlo-authority-v1'
PDF_SYMMETRY_SETTINGS = ('LHAPDF:sSymmetric = off', 'LHAPDF:cSymmetric = on',
                         'LHAPDF:bSymmetric = on')
PDF_INPUT_NAMES = (
    'MSHT20nnlo_as118/MSHT20nnlo_as118.info',
    'MSHT20nnlo_as118/MSHT20nnlo_as118_0000.dat',
    'NNPDF31_nnlo_as_0118/NNPDF31_nnlo_as_0118.info',
    'NNPDF31_nnlo_as_0118/NNPDF31_nnlo_as_0118_0000.dat',
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def hard_pdf_settings(name):
    if name is None:
        return []
    if name not in PRESCRIPTION_NAMES:
        raise ValueError('Unknown DY prescription: ' + str(name))
    return ['PDF:useHard = on', 'PDF:pHardSet = LHAPDF6:MSHT20nnlo_as118',
            *PDF_SYMMETRY_SETTINGS]


def table_filename(name):
    hard_pdf_settings(name)
    return TABLE_NAMES[name]


def prescription_files(name, table_path=None):
    if not hard_pdf_settings(name):
        return {}
    helper = Path(__file__).resolve()
    filename = table_filename(name)
    table = Path(table_path) if table_path is not None else helper.with_name(filename)
    if table_path is None and not table.is_file():
        table = helper.parent.parent / 'data/dark_photon' / filename
    return {HELPER_NAME: helper, filename: table}


def freeze_prescription(name, directory):
    """Create or verify flat, immutable copies; never replace different bytes."""
    result = {}
    for filename, source in prescription_files(name).items():
        payload = source.read_bytes()
        target = Path(directory) / filename
        if target.exists():
            if target.read_bytes() != payload:
                raise ValueError('Frozen DY input differs: ' + str(target))
        else:
            with target.open('xb') as stream:
                stream.write(payload)
        result[filename] = hashlib.sha256(payload).hexdigest()
    return result


def _read_authority(name, table_path):
    files = prescription_files(name, table_path)
    payload = files[table_filename(name)].read_bytes()
    table = json.loads(payload)
    if (table.get('schema') != TABLE_SCHEMA or table.get('name') != name or
            table.get('beam_energy_GeV') != 6800. or
            table.get('hard_pdf') != {'set': 'MSHT20nnlo_as118', 'member': 0} or
            table.get('normalization_reference') != 'native-clipped-LO' or
            table.get('epsilon_reference') != .001 or
            table.get('native_pdf_settings') != list(PDF_SYMMETRY_SETTINGS) or
            table.get('weak_current', {}).get('sin2_theta_w') != .2312 or
            table.get('weak_current', {}).get('m_z_gev') != 91.1876):
        raise ValueError('Unrecognized DY normalization authority')
    maximum = .1 if name == 'msht20-nnlo-v2' else .001
    if table.get('epsilon_upper_bound') != maximum:
        raise ValueError('DY normalization authority has an unqualified epsilon domain')
    if name == 'msht20-nnlo-v2':
        proof = table.get('extended_mixing_qualification', {})
        if (table.get('version') != 2 or proof.get('complete') is not True or
                proof.get('maximum_epsilon') != maximum or
                proof.get('native_width_algebra_passed') is not True or
                proof.get('paired_NNLO_Born_controls_passed') is not True):
            raise ValueError('Extended DY authority lacks its explicit current/width qualification')
    inputs = table.get('pdf_inputs', {})
    if (set(inputs) != set(PDF_INPUT_NAMES) or
            any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
                for value in inputs.values())):
        raise ValueError('Require pinned hard and unchanged CP5 PDF inputs')
    return files, payload, table


def configure_hard_pdf(name, table_path=None):
    """Validate the real GEN data before Pythia loads either PDF set.

    The immutable CMS data root also owns the unchanged CP5 set. Prefixing this
    verified root prevents an inherited SFT or frontend scratch path shadowing
    either set. Alternate workers may carry the same bytes in a local capsule.
    """
    if not hard_pdf_settings(name):
        return None
    files, payload, table = _read_authority(name, table_path)
    candidates = [Path(table['pdf_data_root'])]
    if os.environ.get('CMSSW_BASE'):
        candidates.append(Path(os.environ['CMSSW_BASE']) / 'dy_pdf_inputs')
    candidates.append(Path(__file__).resolve().parent / 'dy_pdf_inputs')
    for directory in candidates:
        if not all((directory / filename).is_file() for filename in table['pdf_inputs']):
            continue
        for filename, expected in table['pdf_inputs'].items():
            if digest(directory / filename) != expected:
                raise ValueError('GEN PDF input differs from the qualified authority: ' + filename)
        previous = os.environ.get('LHAPDF_DATA_PATH', '')
        os.environ['LHAPDF_DATA_PATH'] = str(directory) + (os.pathsep + previous if previous else '')
        return dict(schema='shift-dy-pdf-runtime-v1', data_root=str(directory), files=table['pdf_inputs'])
    raise FileNotFoundError('Qualified MSHT20 and CP5 PDF data are unavailable in this GEN runtime')


def prescription_metadata(name, mass, epsilon, table_path=None):
    if not hard_pdf_settings(name):
        return None
    files, payload, table = _read_authority(name, table_path)
    maximum = table['epsilon_upper_bound']
    if (isinstance(epsilon, bool) or not math.isfinite(epsilon) or not 0 < epsilon <= maximum or
            isinstance(mass, bool) or not math.isfinite(mass)):
        raise ValueError('DY point is outside the qualified mass/epsilon scope')
    rows = table['points']
    masses = [row['mass_gev'] for row in rows]
    if len(set(masses)) != len(masses) or mass not in masses:
        raise ValueError('DY normalization needs an exact qualified mass; no interpolation')
    row = rows[masses.index(mass)]
    factor, error = row['factor'], row['common_numerical_error']
    if (not math.isfinite(factor) or factor <= 0 or not math.isfinite(error) or error < 0 or
            row.get('qualified') is not True):
        raise ValueError('DY normalization point has not passed its theory controls')
    if epsilon < table['epsilon_reference'] and table.get('small_mixing_qualified') is not True:
        raise ValueError('DY current-shape continuation below the reference epsilon is unqualified')
    return dict(schema=SCHEMA, name=name, hard_pdf=table['hard_pdf'], generator_order='LO+PS',
        shower_and_MPI='Unchanged CP5 settings; the override applies to the DY hard process only',
        positive_pdf_policy='Native Pythia8 max(0,xf); matched clipped-Born denominator',
        pdf_inputs=table['pdf_inputs'],
        native_pdf_settings=table['native_pdf_settings'],
        beam_energy_GeV=table['beam_energy_GeV'], epsilon_upper_bound=maximum,
        normalization=dict(order='NNLO', reference=table['normalization_reference'],
            mass_gev=row['mass_gev'], factor=factor, common_numerical_error=error,
            error_scope=('Common quadrature and epsilon-current continuation envelope, not independent per GEN population'
                         if name == 'msht20-nnlo-v2' else
                         'Common numerical normalization error, not an independent error per GEN population'),
            theory_uncertainties=row.get('theory_uncertainties', {})),
        authority=dict(filename=table_filename(name), sha256=hashlib.sha256(payload).hexdigest(),
            helper_filename=HELPER_NAME, helper_sha256=digest(files[HELPER_NAME])),
        weak_current=table['weak_current'], theory_scope=table['theory_scope'])


def apply_prescription(contract, name, table_path=None):
    result = copy.deepcopy(contract)
    if name is None:
        return result
    if result.get('mechanism') != 'pure-on-shell-drell-yan' or result.get('beam_energy_GeV') != 6800.:
        raise ValueError('The DY prescription applies only to native SHIFT DY')
    if 'dy_prescription' in result:
        raise ValueError('DY prescription was already applied')
    metadata = prescription_metadata(name, result['mass_gev'], result['epsilon'], table_path)
    result['process_settings'].extend(hard_pdf_settings(name))
    result['dy_prescription'] = metadata
    return result


def validate_prescription(contract, directory):
    metadata = contract.get('dy_prescription')
    if metadata is None:
        return None
    if directory is None:
        raise ValueError('DY prescription replay requires its frozen source directory')
    directory = Path(directory)
    expected = prescription_metadata(metadata['name'], contract['mass_gev'], contract['epsilon'],
                                     directory / table_filename(metadata['name']))
    if metadata != expected:
        raise ValueError('DY prescription differs from its frozen authority')
    helper = directory / HELPER_NAME
    if not helper.is_file() or digest(helper) != metadata['authority']['helper_sha256']:
        raise ValueError('Frozen DY prescription helper is missing or changed')
    effective = {}
    for setting in contract['process_settings']:
        if '=' in setting:
            key, value = setting.split('=', 1)
            effective[key.strip().lower()] = value.strip().lower()
    for setting in hard_pdf_settings(metadata['name']):
        key, value = setting.split('=', 1)
        if effective.get(key.strip().lower()) != value.strip().lower():
            raise ValueError('GEN hard-PDF settings differ from the DY prescription')
    for key, value in [('standardmodel:sin2thetaw', .2312), ('23:m0', 91.1876)]:
        if key not in effective or float(effective[key]) != value:
            raise ValueError('GEN weak-current settings differ from the NNLO authority')
    if contract.get('mechanism') != 'pure-on-shell-drell-yan' or contract.get('beam_energy_GeV') != 6800.:
        raise ValueError('DY prescription was attached to a different source')
    manifest = json.loads((directory / 'manifest.json').read_text())
    observation = manifest.get('dy_pdf_runtime', {})
    if (observation.get('schema') != 'shift-dy-pdf-runtime-v1' or
            observation.get('files') != metadata['pdf_inputs']):
        raise ValueError('GEN manifest does not prove the qualified hard and CP5 PDF inputs')
    return metadata


def prescription_input_files(contract, directory):
    if validate_prescription(contract, directory) is None:
        return []
    return [Path(directory) / HELPER_NAME, Path(directory) / table_filename(contract['dy_prescription']['name'])]
