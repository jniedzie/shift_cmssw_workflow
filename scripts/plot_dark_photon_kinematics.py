#!/usr/bin/env python3
"""Draw bounded signal-grid kinematics with ROOT, without changing TEA.

Plan format: {"samples": [{"id": "m15_prompt", "mass_gev": 15,
"lifetime_label": "prompt", "generated_events": 20, "nano_path": "..."}]}.
Paths are relative to the plan. Every event has native genWeight=1. Curves
show object counts per full generated event, never cross sections or exposure.
"""
import argparse
from array import array
import hashlib
import json
import math
from pathlib import Path
import sys

from audit_dark_photon_dimuons import GEN_FIELDS, MUON_FIELDS, PAIR_FIELDS, diagnose_event, momentum, MUON_MASS_GEV

LIFETIME_STYLES = {'prompt': 1, 'mean_lab_5m': 2, 'mean_lab_30m': 3, 'validation_displaced': 2}
LIFETIME_TEXT = {'prompt': 'Prompt', 'mean_lab_5m': '5 m mean lab', 'mean_lab_30m': '30 m mean lab',
                 'validation_displaced': 'Displaced test'}
KINEMATICS = [('pt', 'p_{T} [GeV]'), ('eta', '#eta'), ('pz', 'p_{z} [GeV]'), ('p', '|p| [GeV]')]
FIGURES = [
    ('generated_parent_kinematics', 'gen_parent', KINEMATICS, "Generated A' before decay"),
    ('generated_direct_muon_kinematics', 'gen_muon', KINEMATICS, "Direct A' muons after generator radiation"),
    ('generated_direct_dimuon_kinematics', 'gen_pair', [('mass', 'Direct dimuon mass [GeV]'), ('pt', 'Direct dimuon p_{T} [GeV]'), ('eta', 'Direct dimuon #eta')], "Direct generated dimuons after radiation"),
    ('generated_decay_vertices', 'gen_decay', [('z', "A' decay z in CMS [m]"), ('r', "A' decay radius [m]")], "Generated A' decay positions"),
    ('reconstructed_all_muon_kinematics', 'reco_muon_all', KINEMATICS, 'All reconstructed SHIFT muons'),
    ('reconstructed_signal_muon_kinematics', 'reco_muon_signal', KINEMATICS, "Hit-matched direct A' muons"),
    ('reconstructed_all_muon_coordinates', 'reco_muon_all', [('x', 'Track target-line PCA x [cm]'), ('y', 'Track target-line PCA y [cm]'), ('z', 'Track target-line PCA z [m]')], 'All SHIFT muon track coordinates'),
    ('reconstructed_signal_muon_coordinates', 'reco_muon_signal', [('x', 'Track target-line PCA x [cm]'), ('y', 'Track target-line PCA y [cm]'), ('z', 'Track target-line PCA z [m]')], "Hit-matched A' muon track coordinates"),
    ('reconstructed_all_dimuon_kinematics', 'reco_pair_all', [('mass', 'Dimuon mass [GeV]'), ('pt', 'Dimuon p_{T} [GeV]'), ('eta', 'Dimuon #eta')], 'All reconstructed SHIFT dimuon candidates'),
    ('reconstructed_signal_dimuon_kinematics', 'reco_pair_signal', [('mass', 'Dimuon mass [GeV]'), ('pt', 'Dimuon p_{T} [GeV]'), ('eta', 'Dimuon #eta')], "Hit-matched direct A' dimuon candidates"),
    ('reconstructed_all_dimuon_vertices', 'reco_vertex_all', [('x', 'Common-line vertex x [cm]'), ('y', 'Common-line vertex y [cm]'), ('z', 'Common-line vertex z in CMS [m]')], 'All dimuon vertices with valid DCA'),
    ('reconstructed_signal_dimuon_vertices', 'reco_vertex_signal', [('x', 'Common-line vertex x [cm]'), ('y', 'Common-line vertex y [cm]'), ('z', 'Common-line vertex z in CMS [m]')], "Hit-matched A' vertices with valid DCA"),
]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plan_samples(plan):
    if 'samples' in plan:
        return plan['samples']
    samples = []
    for point in plan['points']:
        flight = point['target_mean_lab_flight_m']
        label = 'prompt' if flight is None else f'mean_lab_{float(flight):g}m'
        samples.append(dict(id=point['point'], mass_gev=point['mass_gev'], lifetime_label=label,
                            generated_events=point['events'], epsilon=point['epsilon'],
                            mean_lab_flight_reference_estimate_m=point.get('mean_lab_flight_reference_estimate_m'),
                            nano_path=str(Path(point['detector_directory']) / 'nano.root')))
    return samples


def color_slot(mass):
    return {15.: 0, 30.: 1, 50.: 2, 85.: 3}.get(float(mass), int(round(float(mass) * 1000)) % 4)


def append_object(data, category, obj):
    values = dict(obj)
    if 'pt' in values and 'pz' in values:
        values['p'] = math.hypot(values['pt'], values['pz'])
    if 'vx' in values:
        values.update(x=values['vx'], y=values['vy'], z=values['vz'] / 100.)
    for key in ('pt', 'eta', 'pz', 'p', 'mass', 'x', 'y', 'z', 'r'):
        if key in values:
            value = float(values[key])
            if not math.isfinite(value):
                raise ValueError('Non-finite plotted value')
            data.setdefault(category, {}).setdefault(key, []).append(value)


def collect_event(data, particles, muons, vertices, event_id):
    diagnostic = diagnose_event(particles, muons, vertices, event_id)
    signal_legs = {pair[name]['stable_index'] for pair in diagnostic['direct_pairs'] for name in ('minus', 'plus')}
    signal_vertices = {v['vertex_index'] for pair in diagnostic['direct_pairs'] for v in pair['hit_matched_vertices']}
    for pair in diagnostic['direct_pairs']:
        append_object(data, 'gen_parent', particles[pair['decay_parent_index']])
        for name in ('minus', 'plus'):
            append_object(data, 'gen_muon', particles[pair[name]['stable_index']])
        legs = [momentum(particles[pair[name]['stable_index']], MUON_MASS_GEV) for name in ('minus', 'plus')]
        pair_momentum = [a + b for a, b in zip(*legs)]
        pair_pt = math.hypot(*pair_momentum[:2])
        generated_pair = {'mass': pair['final_muon_pair_mass_gev'], 'pt': pair_pt, 'pz': pair_momentum[2]}
        if pair_pt > 0:
            generated_pair['eta'] = math.asinh(pair_momentum[2] / pair_pt)
        append_object(data, 'gen_pair', generated_pair)
        x, y, z = pair['decay_vertex_cm']
        append_object(data, 'gen_decay', {'z': z / 100., 'r': math.hypot(x, y) / 100.})
    for muon in muons:
        append_object(data, 'reco_muon_all', muon)
        if muon['hitGenPartIdx'] in signal_legs:
            append_object(data, 'reco_muon_signal', muon)
    for i, vertex in enumerate(vertices):
        append_object(data, 'reco_pair_all', vertex)
        if i in signal_vertices:
            append_object(data, 'reco_pair_signal', vertex)
        if vertex['dcaValid'] == 1:
            append_object(data, 'reco_vertex_all', vertex)
            if i in signal_vertices:
                append_object(data, 'reco_vertex_signal', vertex)
    return diagnostic


def binned_counts(values, edges, generated_events):
    """Keep empty events in the denominator and record range tails explicitly."""
    if generated_events <= 0 or len(edges) < 2 or any(a >= b for a, b in zip(edges, edges[1:])):
        raise ValueError('Invalid denominator or histogram edges')
    counts = [0.] * (len(edges) - 1)
    below = above = 0
    for value in values:
        if not math.isfinite(value):
            raise ValueError('Non-finite histogram input')
        if value < edges[0]:
            below += 1
        elif value >= edges[-1]:
            above += 1
        else:
            for i, (low, high) in enumerate(zip(edges, edges[1:])):
                if low <= value < high:
                    counts[i] += 1 / generated_events
                    break
    return counts, {'objects': len(values), 'underflow': below, 'overflow': above,
                    'generated_events': generated_events, 'sum_per_generated_event_including_flow': len(values) / generated_events}


def common_edges(samples, category, variable, bins=30):
    values = [v for sample in samples for v in sample['data'].get(category, {}).get(variable, [])]
    if not values:
        low, high = (-8., 8.) if variable == 'eta' else (-1., 1.)
    else:
        low, high = min(values), max(values)
        if variable in ('pt', 'p', 'mass', 'r'):
            low = 0.
        margin = max((high - low) * .05, .1 if variable != 'z' else 1.)
        low, high = (low if variable in ('pt', 'p', 'mass', 'r') else low - margin), high + margin
    if high <= low:
        high = low + 1.
    return [low + (high - low) * i / bins for i in range(bins + 1)]


def read_sample(spec, plan_dir, allow_subsets=False):
    import ROOT
    source = Path(spec['nano_path'])
    source = source if source.is_absolute() else (plan_dir / source).resolve()
    input_digest = digest(source)
    receipts, completed = {}, {}
    for name in ('signal_report.json', 'report.json'):
        path = source.parent / name
        receipt = json.loads(path.read_text())
        if not receipt.get('complete'):
            raise ValueError('Incomplete Nano receipt: ' + str(path))
        if name == 'report.json' and receipt.get('nano_sha256') != input_digest:
            raise ValueError('Nano digest differs from completed detector receipt')
        receipts[name] = digest(path)
        completed[name] = receipt
    source_path = source.parent / 'source.json'
    source_receipt = json.loads(source_path.read_text())
    if digest(source_path) != completed['report.json']['source_descriptor_sha256']:
        raise ValueError('Changed full-GEN source descriptor')
    receipts['source.json'] = digest(source_path)
    ids = source_receipt['event_ids']
    if (len(ids) != int(spec['generated_events']) or len({tuple(i) for i in ids}) != len(ids)
            or source_receipt['weights'] != [1.] * len(ids)):
        raise ValueError('Full GEN exposure or native weights differ from the plotting plan')
    file = ROOT.TFile.Open(str(source))
    if not file or file.IsZombie() or file.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Unreadable/recovered Nano ROOT file')
    tree = file.Get('Events')
    if not tree:
        raise ValueError('Missing Nano Events tree')
    generated = int(spec['generated_events'])
    entries = int(tree.GetEntries())
    if entries != generated and not (allow_subsets and entries < generated):
        raise ValueError('Plotting requires the full GEN denominator, including zero-reco events')
    branches = {b.GetName() for b in tree.GetListOfBranches()}
    groups = [('GenPart', GEN_FIELDS + ('pz',)),
              ('ShiftMuon', MUON_FIELDS + ('pt', 'eta', 'pz', 'vx', 'vy', 'vz')),
              ('ShiftDimuonVertex', PAIR_FIELDS + ('pt', 'eta', 'pz', 'dcaValid'))]
    required = {f'{prefix}_{field}' for prefix, fields in groups for field in fields} | {'genWeight', 'run', 'luminosityBlock', 'event'}
    if required - branches:
        raise ValueError('Missing plotting fields: ' + ', '.join(sorted(required - branches)))
    data, events = {}, []
    for row in tree:
        if float(row.genWeight) != 1.:
            raise ValueError('This pilot plotter requires native unit weights')
        objects = [[{field: getattr(row, f'{prefix}_{field}')[i] for field in fields}
                    for i in range(getattr(row, f'n{prefix}'))] for prefix, fields in groups]
        events.append(collect_event(data, *objects, [int(row.run), int(row.luminosityBlock), int(row.event)]))
    file.Close()
    processed_ids = [e['event_id'] for e in events]
    if processed_ids != completed['signal_report.json']['requested_event_ids']:
        raise ValueError('Nano identity differs from its signal receipt')
    if not allow_subsets and processed_ids != ids:
        raise ValueError('Nano does not cover the exact full GEN exposure')
    if digest(source) != input_digest:
        raise ValueError('Nano changed while being read')
    return {**spec, 'nano_path': str(source), 'nano_sha256': input_digest,
            'receipt_sha256': receipts, 'processed_events': entries, 'data': data,
            'diagnostic_events': events}


def draw_figures(samples, output):
    import ROOT
    ROOT.gROOT.SetBatch(True)
    ROOT.gStyle.SetOptStat(0)
    ROOT.gStyle.SetTitleFontSize(.06)
    masses = sorted({float(sample['mass_gev']) for sample in samples})
    palette = [ROOT.kAzure + 2, ROOT.kOrange + 7, ROOT.kGreen + 2, ROOT.kMagenta + 1]
    colors = {mass: palette[color_slot(mass)] for mass in masses}
    products, histogram_receipts = [], {}
    root_path = output / 'kinematics.root'
    overview_path = output / 'dark_photon_kinematics_overview.pdf'
    root_output = ROOT.TFile(str(root_path), 'RECREATE')
    histogram_names = []
    for figure_index, (name, category, variables, title) in enumerate(FIGURES):
        canvas = ROOT.TCanvas(name, title, 430 * len(masses), 280 * len(variables) + 90)
        grid = ROOT.TPad(name + '_grid', '', 0., 0., 1., .92)
        grid.Draw(); grid.cd(); grid.Divide(len(masses), len(variables), .008, .008)
        keep = [grid]
        for row_index, (variable, axis_label) in enumerate(variables):
            edges = common_edges(samples, category, variable)
            for col_index, mass in enumerate(masses):
                pad = grid.cd(row_index * len(masses) + col_index + 1)
                pad.SetLeftMargin(.19); pad.SetBottomMargin(.18); pad.SetTopMargin(.12)
                curves = []
                selected = [s for s in samples if float(s['mass_gev']) == mass]
                selected.sort(key=lambda s: list(LIFETIME_STYLES).index(s['lifetime_label']))
                maximum = 0.
                for spec in selected:
                    values = spec['data'].get(category, {}).get(variable, [])
                    counts, receipt = binned_counts(values, edges, spec['generated_events'])
                    key = f'{name}/{mass:g}/{spec["id"]}/{variable}'
                    histogram_receipts[key] = receipt
                    histogram = ROOT.TH1D(key.replace('/', '_'), f"m_{{A'}} = {mass:g} GeV;{axis_label};Objects / generated event / bin", len(edges) - 1, array('d', edges))
                    histogram.SetDirectory(0)
                    for i, count in enumerate(counts, 1):
                        histogram.SetBinContent(i, count)
                        histogram.SetBinError(i, math.sqrt(count * spec['generated_events']) / spec['generated_events'])
                    for bin_index, flow in ((0, receipt['underflow']), (len(edges), receipt['overflow'])):
                        histogram.SetBinContent(bin_index, flow / spec['generated_events'])
                        histogram.SetBinError(bin_index, math.sqrt(flow) / spec['generated_events'])
                    histogram.SetEntries(len(values))
                    histogram.SetLineColor(colors[mass]); histogram.SetLineWidth(3)
                    histogram.SetLineStyle(LIFETIME_STYLES[spec['lifetime_label']])
                    histogram.GetXaxis().SetTitleSize(.055); histogram.GetXaxis().SetLabelSize(.047)
                    histogram.GetYaxis().SetTitleSize(.045); histogram.GetYaxis().SetLabelSize(.045)
                    histogram.GetYaxis().SetTitleOffset(1.7)
                    maximum = max(maximum, max(counts))
                    root_output.cd(); histogram.Write(); histogram_names.append(histogram.GetName()); pad.cd()
                    curves.append((histogram, spec, receipt))
                for i, (histogram, _, _) in enumerate(curves):
                    histogram.SetMinimum(0.); histogram.SetMaximum(max(.05, maximum * 1.6))
                    histogram.Draw('HIST' if i == 0 else 'HIST SAME')
                    keep.append(histogram)
                legend = ROOT.TLegend(.35, .66, .96, .88)
                legend.SetBorderSize(0); legend.SetFillStyle(0); legend.SetTextSize(.028)
                for histogram, spec, receipt in curves:
                    label = LIFETIME_TEXT[spec['lifetime_label']] + f': {receipt["objects"]} obj / N{spec["generated_events"]}'
                    if spec['processed_events'] != spec['generated_events']:
                        label += f' [Nano {spec["processed_events"]}/{spec["generated_events"]}]'
                    legend.AddEntry(histogram, label, 'l')
                legend.Draw(); keep.append(legend)
                if maximum == 0:
                    text = ROOT.TLatex(.25, .4, 'No candidates in these MC samples')
                    text.SetNDC(); text.SetTextSize(.045); text.Draw(); keep.append(text)
        canvas.cd()
        scope = 'VALIDATION SUBSET' if any(s['processed_events'] != s['generated_events'] for s in samples) else 'small MC'
        annotation = ROOT.TLatex(.02, .965, title)
        annotation.SetNDC(); annotation.SetTextSize(.024); annotation.Draw(); keep.append(annotation)
        note = ROOT.TLatex(.02, .93, 'Objects per full GEN event, native W=1 | ' + scope + ', no recording-efficiency claim')
        note.SetNDC(); note.SetTextSize(.016); note.Draw(); keep.append(note)
        if figure_index == 0:
            canvas.Print(str(overview_path) + '[')
        for extension in ('png', 'pdf'):
            path = output / (name + '.' + extension)
            canvas.SaveAs(str(path))
            products.append({'path': str(path), 'sha256': digest(path), 'bytes': path.stat().st_size})
        canvas.Print(str(overview_path))
        if figure_index == len(FIGURES) - 1:
            canvas.Print(str(overview_path) + ']')
        canvas.Close()
    root_output.Close()
    check = ROOT.TFile.Open(str(root_path))
    if not check or check.IsZombie() or check.TestBit(ROOT.TFile.kRecovered) or check.GetNkeys() != len(histogram_names):
        raise ValueError('Incomplete kinematics ROOT histogram output')
    for name in histogram_names:
        histogram = check.Get(name)
        if not histogram or not all(math.isfinite(histogram.GetBinContent(i)) and math.isfinite(histogram.GetBinError(i))
                                    for i in range(histogram.GetNcells())):
            raise ValueError('Invalid kinematics ROOT histogram: ' + name)
    check.Close()
    products.append({'path': str(root_path), 'sha256': digest(root_path), 'bytes': root_path.stat().st_size,
                     'histogram_count': len(histogram_names), 'normalization': 'objects per full generated event including flow'})
    if not overview_path.read_bytes().startswith(b'%PDF'):
        raise ValueError('Invalid multipage overview PDF')
    products.append({'path': str(overview_path), 'sha256': digest(overview_path), 'bytes': overview_path.stat().st_size,
                     'pages': len(FIGURES)})
    return products, histogram_receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--allow-subsets', action='store_true', help='Explicit validation-only incomplete Nano exposure')
    parser.add_argument('--allow-incomplete', action='store_true', help='Plot only completed grid points and report skipped points')
    args = parser.parse_args()
    plan_path = args.plan.resolve()
    plan = json.loads(plan_path.read_text())
    specs = plan_samples(plan)
    if not specs or len({s['id'] for s in specs}) != len(specs):
        raise ValueError('Require nonempty uniquely named sample list')
    for spec in specs:
        if spec['lifetime_label'] not in LIFETIME_STYLES or int(spec['generated_events']) <= 0:
            raise ValueError('Unsupported lifetime label or generated-event denominator')
    if args.output_dir.exists():
        raise FileExistsError('Require a new output directory')
    ready, skipped = [], []
    for spec in specs:
        path = Path(spec['nano_path'])
        path = path if path.is_absolute() else plan_path.parent / path
        status_path = path.parent / 'signal_report.json'
        available = path.exists() and status_path.exists() and json.loads(status_path.read_text()).get('complete') is True
        if not available and args.allow_incomplete:
            skipped.append(spec['id'])
        else:
            ready.append(spec)
    if not ready:
        raise ValueError('No completed Nano samples to plot')
    samples = [read_sample(spec, plan_path.parent, args.allow_subsets) for spec in ready]
    args.output_dir.mkdir(parents=True)
    products, histograms = draw_figures(samples, args.output_dir)
    receipt = {'schema': 'shift-dark-photon-grid-kinematics-v1', 'complete': True,
               'command': [sys.executable, *sys.argv], 'plan_sha256': digest(plan_path),
               'script_sha256': digest(Path(__file__)),
               'mother_helper_sha256': digest(Path(__file__).with_name('audit_dark_photon_dimuons.py')),
               'normalization': 'native unit-weight objects per full generated event; no cross-section or exposure scaling',
               'validation_subset': args.allow_subsets, 'recording_efficiency_validated': False,
               'skipped_incomplete_points': skipped, 'full_grid_complete': not skipped,
               'muon_coordinate_meaning': 'field-aware target-line track PCA, not the truth decay vertex',
               'dimuon_vertex_scope': 'persisted common-line vertices with dcaValid=1; no added selection',
               'samples': samples, 'histogram_accounting': histograms, 'products': products}
    (args.output_dir / 'kinematics_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f'Wrote {len(FIGURES)*2} single PNG/PDF plots, overview PDF and ROOT histograms for {len(samples)} samples: {args.output_dir}')


if __name__ == '__main__':
    main()
