"""Configure and audit residual muon-producing decays in saved GEN events."""
import math


def configure_decays(process, seed, timing_persisted):
    import FWCore.ParameterSet.Config as cms
    label = 'shiftEventTime' if timing_persisted else 'shiftMuonDecays'
    src = 'shiftEventTime' if timing_persisted else 'generatorSmeared'
    setattr(process, label, cms.EDProducer('ShiftMuonDecayProducer',
        src=cms.InputTag(src, '', '@skipCurrentProcess'),
        decayCylinderRadiusMm=cms.double(8000.),
        decayCylinderHalfLengthMm=cms.double(151000.)))
    setattr(process.RandomNumberGeneratorService, label, cms.PSet(
        initialSeed=cms.untracked.uint32(seed + 77),
        engineName=cms.untracked.string('HepJamesRandom')))
    if not timing_persisted:
        process.shiftEventTime.src = cms.InputTag(label)
    process.generatorSmeared.currentTag = cms.untracked.InputTag('shiftEventTime')
    from PhysicsTools.HepMCCandAlgos.genParticles_cfi import genParticles
    process.genParticles = genParticles.clone(src=cms.InputTag('generatorSmeared'))
    sequence = getattr(process, label)
    if not timing_persisted:
        sequence = sequence + process.shiftEventTime
    process.simulation_step = cms.Path(sequence + process.generatorSmeared + process.genParticles + process.psim)
    process.simulation_step.associate(process.PPSTransportTask)
    process.schedule = cms.Schedule(process.simulation_step, process.endjob_step, process.FEVTDEBUGoutput_step)
    process.FEVTDEBUGoutput.outputCommands.extend([
        'keep *_shiftMuonDecays_*_*', 'keep *_shiftEventTime_*_*',
        'drop *_genParticles_*_*', 'keep *_genParticles_*_SHIFTSIM'])


def audit_completed_decays(original, simulated, original_indices, count, timing_persisted):
    """Require unchanged original momenta/spacetime and conserved new decays."""
    from DataFormats.FWLite import Events, Handle
    old, new = Events(str(original)), Events(str(simulated))
    totals = dict(events=count, decayed_parents=0, added_muons=0,
                  maximum_momentum_residual_GeV=0.)
    species = {}

    def hep(events):
        handle = Handle('edm::HepMCProduct')
        events.getByLabel('shiftEventTime', handle)
        if not handle.isValid():
            events.getByLabel('generatorSmeared', handle)
        if not handle.isValid():
            raise ValueError('Missing decay-audit HepMC')
        return handle, handle.product().GetEvent()

    def particles(event):
        result = {}
        it, end = event.particles_begin(), event.particles_end()
        while it != end:
            p = it.__deref__(); it.__preinc__()
            result[int(p.barcode())] = p
        return result

    for output_index, input_index in enumerate(original_indices):
        old.to(input_index); new.to(output_index)
        old_handle, before = hep(old)
        new_handle, after = hep(new)
        initial, final = particles(before), particles(after)
        source = before.signal_process_vertex() or before.vertices_begin().__deref__()
        shift = 0. if timing_persisted else -float(source.position().z())
        label = 'shiftEventTime' if timing_persisted else 'shiftMuonDecays'
        enabled = Handle('std::vector<int>')
        new.getByLabel(label, 'enabledPdgIds', enabled)
        if not enabled.isValid():
            raise ValueError('Missing residual-decay enabled-species audit')
        allowed = set(map(int, enabled.product()))
        for barcode, p in initial.items():
            q = final.get(barcode)
            if q is None or int(q.pdg_id()) != int(p.pdg_id()):
                raise ValueError('Decay completion changed original particle identities')
            a, b = p.momentum(), q.momentum()
            if any(not math.isclose(float(x), float(y), rel_tol=1e-12, abs_tol=1e-10)
                   for x, y in zip((a.px(), a.py(), a.pz(), a.e()), (b.px(), b.py(), b.pz(), b.e()))):
                raise ValueError('Decay completion changed original momentum')
            if p.production_vertex():
                if not q.production_vertex():
                    raise ValueError('Decay completion lost original production vertex')
                a, b = p.production_vertex().position(), q.production_vertex().position()
                if any(not math.isclose(float(x), float(y), rel_tol=1e-12, abs_tol=1e-7)
                       for x, y in zip((a.x(), a.y(), a.z(), a.t()+shift), (b.x(), b.y(), b.z(), b.t()))):
                    raise ValueError('Decay completion changed original spacetime')
            if int(p.status()) != int(q.status()):
                if (int(p.status()) != 1 or int(q.status()) != 2 or p.end_vertex()
                        or not q.end_vertex() or abs(int(p.pdg_id())) not in allowed):
                    raise ValueError('Unexpected original particle status change')
                key = str(abs(int(p.pdg_id())))
                species[key] = species.get(key, 0) + 1
                totals['decayed_parents'] += 1
        for barcode, p in final.items():
            if barcode not in initial and int(p.status()) == 1 and abs(int(p.pdg_id())) == 13:
                totals['added_muons'] += 1
        original_vertices = set()
        vi, ve = before.vertices_begin(), before.vertices_end()
        while vi != ve:
            original_vertices.add(int(vi.__deref__().barcode())); vi.__preinc__()
        it, end = after.vertices_begin(), after.vertices_end()
        while it != end:
            v = it.__deref__(); it.__preinc__()
            if int(v.barcode()) in original_vertices:
                continue
            incoming, outgoing = [], []
            for direction, result in [('in', incoming), ('out', outgoing)]:
                pi = getattr(v, 'particles_'+direction+'_const_begin')()
                pe = getattr(v, 'particles_'+direction+'_const_end')()
                while pi != pe:
                    result.append(pi.__deref__().momentum()); pi.__preinc__()
            if not incoming or not outgoing:
                raise ValueError('Appended decay vertex has missing particles')
            for component in ('px', 'py', 'pz', 'e'):
                a = math.fsum(float(getattr(p, component)()) for p in incoming)
                b = math.fsum(float(getattr(p, component)()) for p in outgoing)
                residual = abs(a-b)
                totals['maximum_momentum_residual_GeV'] = max(totals['maximum_momentum_residual_GeV'], residual)
                if residual > 1e-6 * max(1., abs(a)):
                    raise ValueError('Appended decay violates four-momentum conservation')
    totals['decayed_parent_pdg_counts'] = species
    return totals
