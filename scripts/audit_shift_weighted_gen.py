#!/usr/bin/env python3
"""Audit fixed-trial weighted GEN, using the named ledger rather than native counters.

This checks generator bookkeeping and complementary event ownership. It does
not establish inclusive closure, absolute decay normalization or CMS physics
readiness. No reconstructed or detector content is inspected.
"""
import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

from generation_publication import sha256
from soft_mpi_model import DIRECT_JPSI_IDS, MODEL_CONTRACT, PARTITION_CONTRACT

LEDGER_SCHEMA = 'shift-weighted-fixed-trial-ledger-v3'
ALGORITHM = 'independent-geometric-full-support-tail-channel-retry-rb-v4'
# Ordered common+CP5+process commands from the preserved ordinary source,
# canonicalized as JSON [key,value] pairs. Only 443 decay and the proposal
# abort guard are excluded. This also rejects extra physics settings and
# reordered Tune commands, whose order changes Pythia's effective model.
SOURCE_MODEL_SHA256 = 'a6bb0fd762c5805f1cb2e08c50e713d53ae41a20399adb9c748ea91fb7f8e50e'
# Resolved CP5 settings from the unchanged frozen source used by the ordinary
# reference. Later process blocks must not silently override this model.
CP5_SETTINGS = {
    'Tune:pp': '14', 'Tune:ee': '7', 'MultipartonInteractions:ecmPow': '0.03344',
    'MultipartonInteractions:bProfile': '2', 'MultipartonInteractions:pT0Ref': '1.41',
    'MultipartonInteractions:coreRadius': '0.7634', 'MultipartonInteractions:coreFraction': '0.63',
    'ColourReconnection:range': '5.176', 'SigmaTotal:zeroAXB': 'off',
    'SpaceShower:alphaSorder': '2', 'SpaceShower:alphaSvalue': '0.118',
    'SigmaProcess:alphaSvalue': '0.118', 'SigmaProcess:alphaSorder': '2',
    'MultipartonInteractions:alphaSvalue': '0.118', 'MultipartonInteractions:alphaSorder': '2',
    'TimeShower:alphaSorder': '2', 'TimeShower:alphaSvalue': '0.118',
    'SigmaTotal:mode': '0', 'SigmaTotal:sigmaEl': '21.89', 'SigmaTotal:sigmaTot': '100.309',
    'PDF:pSet': 'LHAPDF6:NNPDF31_nnlo_as_0118',
}


def integer(record, key, minimum=0):
    value = record[key]
    if type(value) is not int or value < minimum:
        raise ValueError('Invalid integer ledger field: '+key)
    return value


def number(record, key, minimum=0):
    value = record[key]
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError('Invalid finite ledger field: '+key)
    return float(value)


def close(actual, expected, label):
    # Preserve sensitivity for tiny tail weights; an absolute 1e-9 tolerance
    # would silently accept a zero weight sum in these samples.
    if not math.isfinite(actual) or not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-300):
        raise ValueError(f'{label} differs: {actual!r} != {expected!r}')


def validate_ledger(ledger):
    if ledger.get('schema') != LEDGER_SCHEMA or ledger.get('algorithm') != ALGORITHM:
        raise ValueError('Unsupported weighted proposal schema or algorithm')
    if ledger.get('complete') is not True or ledger.get('native_gen_lumi_is_trial_ledger') is not False:
        raise ValueError('Incomplete or ambiguous fixed-trial ledger')
    if ledger.get('physics_valid') is not False or ledger.get('normalization_ready') is not False:
        raise ValueError('Weighted GEN must retain its open physics/normalization gates')
    if ledger.get('sample') not in ('qcd', 'direct_jpsi'):
        raise ValueError('Unknown complementary event class')
    lower, upper = number(ledger, 'lower'), float(ledger['upper'])
    if lower <= 0 or not math.isfinite(upper) or not (upper == -1 or upper > lower):
        raise ValueError('Invalid half-open weighted partition bounds')
    trials = integer(ledger, 'requested_trials', 2)
    if any(integer(ledger, field) != trials for field in ('framework_slots', 'tried', 'last_parent_event')):
        raise ValueError('Framework/native trials differ from the fixed requested budget')
    if integer(ledger, 'run', 30000000) > 4294967295 or any(integer(ledger, f) != 1 for f in
            ('lumi', 'first_parent_event', 'initializations')):
        raise ValueError('Invalid fixed-trial source namespace or initialization count')
    if any(integer(ledger, field) for field in ('unexpected_failures', 'accounting_failures')):
        raise ValueError('Unexpected generator or accounting failure')
    calls = integer(ledger, 'proposal_calls', trials)
    if integer(ledger, 'accounted_proposal_calls') != calls:
        raise ValueError('Unaccounted internal proposal/retry calls')
    max_calls = integer(ledger, 'max_proposal_calls_per_slot', 1)
    retries = integer(ledger, 'intrinsic_retry_slots')
    if not trials <= calls <= trials*max_calls or retries > trials or (calls > trials and not retries):
        raise ValueError('Inconsistent intrinsic retry accounting')
    if integer(ledger, 'ordinary_proposal_calls') > calls:
        raise ValueError('Ordinary full-support proposal calls exceed all calls')
    close(number(ledger, 'ordinary_proposal_fraction'), .1, 'Full-support ordinary proposal fraction')
    accepted = integer(ledger, 'accepted')
    if accepted > trials or integer(ledger, 'accumulated_accepted') != accepted:
        raise ValueError('Inconsistent accepted-event count')
    statuses = ledger['status_counts']
    if (not isinstance(statuses, list) or len(statuses) != 16 or
            any(type(x) is not int or x < 0 for x in statuses) or
            sum(statuses) != trials or statuses[0] != accepted):
        raise ValueError('Native terminal statuses differ from the fixed trial ledger')
    for field in ('sumw', 'sumw2', 'sigma_mb', 'sigma_error_mb'):
        number(ledger, field)
    if number(ledger, 'sigma_nd_mb') <= 0:
        raise ValueError('Missing positive source non-diffractive cross section')
    return trials


def _keyword(call, name):
    if not isinstance(call, ast.Call):
        raise ValueError('Resolved configuration contains a nonliteral module')
    values = [keyword.value for keyword in call.keywords if keyword.arg == name]
    if len(values) != 1:
        raise ValueError('Missing/duplicate resolved configuration field: '+name)
    return values[0]


def _value(node):
    if isinstance(node, ast.Call):
        if len(node.args) != 1:
            raise ValueError('Expected a literal scalar CMS parameter')
        node = node.args[0]
    return ast.literal_eval(node)


def _strings(node):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != 'vstring':
        raise ValueError('Expected resolved literal Pythia string settings')
    values = [ast.literal_eval(arg) for arg in node.args]
    if len(values) == 1 and isinstance(values[0], (tuple, list)):
        values = list(values[0])
    if not all(isinstance(value, str) for value in values):
        raise ValueError('Nonliteral Pythia string settings')
    return values


def validate_config(source, ledger):
    """Read resolved configuration without importing/executing arbitrary code."""
    modules = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'process':
                    if target.attr in modules:
                        raise ValueError('Duplicate resolved process assignment: '+target.attr)
                    modules[target.attr] = node.value
    generator = modules['generator']
    if not generator.args or _value(generator.args[0]) != 'Pythia8GeneratorFilter':
        raise ValueError('Unsupported weighted GEN producer')
    pythia = _keyword(generator, 'PythiaParameters')
    names = _strings(_keyword(pythia, 'parameterSets'))
    if len(set(names)) != len(names) or 'pythia8CP5Settings' not in names or 'processParameters' not in names:
        raise ValueError('Missing CP5/process settings or duplicate parameter blocks')
    parameters, settings = [], {}
    for name in names:
        for text in _strings(_keyword(pythia, name)):
            key, value = re.split(r'\s*=\s*|\s+', text.strip(), maxsplit=1)
            parameters.append([key, value])
            settings[key] = value
    required = {'SoftQCD:all': 'off', 'SoftQCD:nonDiffractive': 'on', 'HardQCD:all': 'off',
                'Charmonium:all': 'off', 'Bottomonium:all': 'off',
                'MultipartonInteractions:processLevel': '3', 'MultipartonInteractions:bProfile': '2',
                'Beams:frameType': '2', 'Beams:idA': '2212', 'Beams:idB': '2212',
                'Check:abortIfVeto': 'on'}
    if any(settings.get(key) != value for key, value in required.items()):
        raise ValueError('Fixed source model or retry settings changed')
    for key, expected in CP5_SETTINGS.items():
        actual = settings.get(key)
        try:
            matches = actual is not None and float(actual) == float(expected)
        except ValueError:
            matches = actual == expected
        if not matches:
            raise ValueError('Changed frozen CP5 model setting: '+key)
    if float(settings['Beams:eA']) != 0 or float(settings['Beams:eB']) != 6800:
        raise ValueError('Changed fixed-target beam energy')
    decays = {key: value for key, value in settings.items() if key.startswith('443:')}
    if decays != ({'443:onMode': 'off', '443:onIfMatch': '13 -13'} if ledger['sample'] == 'direct_jpsi' else {}):
        raise ValueError('Changed direct-J/psi forced-decay convention')
    customizations = _keyword(generator, 'UserCustomization')
    if not isinstance(customizations, ast.Call) or len(customizations.args) != 1:
        raise ValueError('Require exactly one weighted proposal hook')
    hook = customizations.args[0]
    expected = dict(pluginName='ShiftMpiWeightedProposalHook', eventClass=ledger['sample'],
                    eventRun=ledger['run'], proposalTrials=ledger['requested_trials'],
                    pTHatMin=ledger['lower'], pTHatMax=ledger['upper'])
    if any(_value(_keyword(hook, key)) != value for key, value in expected.items()):
        raise ValueError('Resolved proposal hook differs from fixed-trial ledger')
    source_module = modules['source']
    if not source_module.args or _value(source_module.args[0]) != 'EmptySource':
        raise ValueError('Require the fixed-trial EmptySource')
    for key, expected in [('firstRun', ledger['run']), ('firstEvent', 1), ('firstLuminosityBlock', 1)]:
        if _value(_keyword(source_module, key)) != expected:
            raise ValueError('Resolved source event namespace differs')
    for key in ('numberEventsInRun', 'numberEventsInLuminosityBlock'):
        if _value(_keyword(source_module, key)) <= ledger['requested_trials']:
            raise ValueError('Resolved source could split the fixed trials across runs/lumis')
    if _value(_keyword(modules['maxEvents'], 'input')) != ledger['requested_trials']:
        raise ValueError('Resolved framework request differs from trial budget')
    for key in ('numberOfThreads', 'numberOfStreams'):
        if _value(_keyword(modules['options'], key)) != 1:
            raise ValueError('Weighted hook requires the resolved single-thread/single-stream contract')
    normalization = modules['shiftWeightedNormalization']
    if not normalization.args or _value(normalization.args[0]) != 'ShiftWeightedGenRunInfoProducer':
        raise ValueError('Missing named fixed-trial normalization producer')
    if _value(_keyword(normalization, 'statisticsFile')) != _value(_keyword(hook, 'statisticsFile')):
        raise ValueError('Normalization producer reads a different ledger')
    model = [row for row in parameters if not row[0].startswith('443:') and row[0] != 'Check:abortIfVeto']
    model_digest = hashlib.sha256(json.dumps(model, separators=(',', ':')).encode()).hexdigest()
    if model_digest != SOURCE_MODEL_SHA256:
        raise ValueError('Ordered source model differs from the frozen ordinary reference')
    return model_digest


def validate_snapshot(ledger, embedded_ledger, records, weighted_run_info):
    trials = validate_ledger(ledger)
    if embedded_ledger != ledger:
        raise ValueError('Named EDM fixedTrialLedger differs from JSON sidecar')
    if len(records) != ledger['accepted']:
        raise ValueError('EDM event count differs from fixed-trial accepted count')
    identities = set()
    for record in records:
        identity = tuple(record['event_id'])
        if (len(identity) != 3 or any(type(x) is not int for x in identity) or
                identity[0] != ledger['run'] or identity[1] != 1 or not 1 <= identity[2] <= trials or identity in identities):
            raise ValueError('Duplicate or out-of-budget accepted event identity')
        identities.add(identity)
        weight = record['weight']
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError('Nonpositive/nonfinite event weight')
        close(record['hepmc_weight'], weight, 'HepMC nominal weight')
        if record['process'] != 101 or bool(record['direct_scales']) != (ledger['sample'] == 'direct_jpsi'):
            raise ValueError('Complementary direct-hard-J/psi ownership violation')
        if any(not math.isfinite(scale) or scale < 0 for scale in record['direct_scales']):
            raise ValueError('Invalid direct-hard-J/psi scale')
        scale = max(record['direct_scales']) if record['direct_scales'] else record['pthat']
        if not math.isfinite(scale) or scale < ledger['lower'] or (ledger['upper'] != -1 and scale >= ledger['upper']):
            raise ValueError('Event outside its half-open physical scale bin')
        record['scale'] = scale
        if type(record['charged']) is not int or record['charged'] < 0:
            raise ValueError('Invalid final stable charged-particle multiplicity')
    sumw = math.fsum(record['weight'] for record in records)
    sumw2 = math.fsum(record['weight']**2 for record in records)
    close(number(ledger, 'sumw'), sumw, 'Ledger sum of accepted weights')
    close(number(ledger, 'sumw2'), sumw2, 'Ledger sum of squared accepted weights')
    sigma = ledger['sigma_nd_mb']*sumw/trials
    variance = max(0., math.fsum((sumw2, -sumw*sumw/trials)))
    error = ledger['sigma_nd_mb']*math.sqrt(variance/(trials*(trials-1)))
    close(number(ledger, 'sigma_mb'), sigma, 'Fixed-trial cross section')
    close(number(ledger, 'sigma_error_mb'), error, 'Fixed-trial cross-section error')
    close(weighted_run_info['internal_xsec_pb'], sigma*1e9, 'Named run cross section')
    close(weighted_run_info['error_pb'], error*1e9, 'Named run cross-section error')
    close(weighted_run_info['filter_efficiency'], 1., 'Named run filter efficiency')
    return dict(events=len(records), event_ids=[record['event_id'] for record in records],
                weights=[record['weight'] for record in records], scale=[record['scale'] for record in records],
                charged=[record['charged'] for record in records], sumw=sumw, sumw2=sumw2,
                sigma_weighted_mb=sigma, error_weighted_mb=error,
                effective_events=sumw*sumw/sumw2 if sumw2 else 0.)


def read_edm(path, expected_events):
    import ROOT
    from DataFormats.FWLite import Events, Handle, Runs

    opened = ROOT.TFile.Open(str(path))
    if not opened or opened.IsZombie() or opened.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Unreadable or recovered GEN ROOT file')
    try:
        tree = opened.Get('Events')
        if not tree or int(tree.GetEntries()) != expected_events:
            raise ValueError('ROOT Events count differs from fixed-trial ledger')
    finally:
        opened.Close()

    def get(item, label, kind):
        handle = Handle(kind)
        item.getByLabel(label, handle)
        if not handle.isValid():
            raise ValueError(f'Missing EDM product: {label} ({kind})')
        return handle.product()

    records, beams = [], None
    for event in Events(str(path)):
        auxiliary = event.eventAuxiliary()
        info = get(event, 'generator', 'GenEventInfoProduct')
        hepmc = get(event, ('generator', 'unsmeared'), 'edm::HepMCProduct').GetEvent()
        nominal = list(hepmc.weights())
        if not nominal:
            raise ValueError('Missing HepMC nominal weight')
        bins = list(info.binningValues())
        if not bins or not math.isfinite(float(bins[0])) or float(bins[0]) < 0:
            raise ValueError('Invalid stored hardest-MPI pThat')
        pair = hepmc.beam_particles()
        if not pair.first or not pair.second:
            raise ValueError('Missing fixed-target HepMC beams')
        a, b = pair.first.momentum(), pair.second.momentum()
        momenta = [float(x) for p in (a, b) for x in (p.px(), p.py(), p.pz(), p.e())]
        if (not all(math.isfinite(x) for x in momenta) or
                max(abs(a.px()), abs(a.py()), abs(a.pz()), abs(b.px()), abs(b.py())) > 1e-6 or
                abs(a.e()-.938272) > 1e-4 or abs(b.e()-6800.) > 1e-4 or b.pz() >= 0 or
                abs(pair.first.pdg_id()) != 2212 or abs(pair.second.pdg_id()) != 2212):
            raise ValueError('Changed fixed-target proton beams')
        beams = dict(target_energy_GeV=float(a.e()), projectile_energy_GeV=float(b.e()),
                     projectile_pz_GeV=float(b.pz()))
        direct = []
        particle, end = hepmc.particles_begin(), hepmc.particles_end()
        while particle != end:
            item = particle.__deref__()
            particle.__preinc__()
            if abs(item.pdg_id()) in DIRECT_JPSI_IDS and abs(item.status()) in (23, 33):
                direct.append(float(item.momentum().perp()))
        charged = 0
        for particle in get(event, 'genParticles', 'std::vector<reco::GenParticle>'):
            if not math.isfinite(float(particle.charge())):
                raise ValueError('Invalid generated particle charge')
            charged += int(particle.status()) == 1 and float(particle.charge()) != 0.
        records.append(dict(event_id=[int(auxiliary.run()), int(auxiliary.luminosityBlock()), int(auxiliary.event())],
                            weight=float(info.weight()), hepmc_weight=float(nominal[0]),
                            process=int(info.signalProcessID()), pthat=float(bins[0]), direct_scales=direct,
                            charged=charged))
    runs = []
    for run in Runs(str(path)):
        ledger = json.loads(str(get(run, ('shiftWeightedNormalization', 'fixedTrialLedger'), 'std::string')))
        if int(run.runAuxiliary().run()) != ledger['run']:
            raise ValueError('EDM run namespace differs from named fixed-trial ledger')
        product = get(run, 'shiftWeightedNormalization', 'GenRunInfoProduct')
        cross_section = product.internalXSec()
        weighted = dict(internal_xsec_pb=float(cross_section.value()), error_pb=float(cross_section.error()),
                        filter_efficiency=float(product.filterEfficiency()))
        native = get(run, 'generator', 'GenRunInfoProduct').internalXSec()
        runs.append((ledger, weighted, dict(internal_xsec_pb=float(native.value()), error_pb=float(native.error()))))
    if len(runs) != 1:
        raise ValueError('Require exactly one EDM run with named fixed-trial normalization')
    return records, runs[0][0], runs[0][1], runs[0][2], beams


def audit(input_path, ledger_path, config_path):
    inputs = [Path(input_path), Path(ledger_path), Path(config_path)]
    before = [(path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns) for path in inputs]
    ledger_bytes, config_bytes = inputs[1].read_bytes(), inputs[2].read_bytes()
    ledger = json.loads(ledger_bytes)
    validate_ledger(ledger)
    model_digest = validate_config(config_bytes.decode(), ledger)
    records, embedded, weighted_run, native_run, beams = read_edm(inputs[0], ledger['accepted'])
    report = validate_snapshot(ledger, embedded, records, weighted_run)
    report.update(schema='shift-weighted-gen-semantic-audit-v1', complete=True, healthy=True,
                  requested=ledger['requested_trials'], accepted=ledger['accepted'], tried=ledger['tried'],
                  proposal_calls=ledger['proposal_calls'], sample=ledger['sample'],
                  bounds=[ledger['lower'], ledger['upper']], algorithm=ALGORITHM, ledger=ledger,
                  weighted_run_info=weighted_run, native_generator_run_info=native_run,
                  native_generator_run_is_authority=False, native_gen_lumi_is_trial_ledger=False,
                  source_model_contract=MODEL_CONTRACT, partition_contract=PARTITION_CONTRACT,
                  source_model_settings_sha256=model_digest, beams=beams,
                  charged_definition='CMSSW genParticles status 1 with nonzero charge; final converted HepMC particles',
                  input_sha256=sha256(inputs[0]), ledger_sha256=hashlib.sha256(ledger_bytes).hexdigest(),
                  config_sha256=hashlib.sha256(config_bytes).hexdigest(),
                  auditor_sha256=sha256(Path(__file__)),
                  physics_valid=False, normalization_ready=False,
                  forced_decay=('443 -> 13 -13; branching convention remains an independent gate'
                                if ledger['sample'] == 'direct_jpsi' else 'none'))
    after = [(path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns) for path in inputs]
    if before != after:
        raise ValueError('GEN audit inputs changed while being inspected')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--ledger', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = audit(args.input, args.ledger, args.config)
    temporary = output.with_name(output.name+'.partial.'+uuid.uuid4().hex)
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    temporary.replace(output)
    print(f"Validated {report['events']} accepted weighted GEN events in {report['tried']} fixed native trials")


if __name__ == '__main__':
    main()
