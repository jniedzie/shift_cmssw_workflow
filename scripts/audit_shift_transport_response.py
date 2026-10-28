#!/usr/bin/env python3
# Code style: AI-code. AI-generated passive GEN-to-transport response audit.
"""Read native SIM, passive logs and reco associations without selecting events.

Track IDs are Geant4 IDs, not indices in the saved SimTrack vector. A missing
saved track or log record is unknown; it is never evidence of absorption.
"""
from collections import Counter
import ctypes
import json
import math


def native_snapshot(path):
    """Retain the complete native truth/hit state for an exact paired comparison."""
    import ROOT
    from DataFormats.FWLite import Events, Handle
    from audit_shift_material_scout import metadata, graph, identity, product, items
    ROOT.gSystem.Load('libDataFormatsMuonDetId')
    if not hasattr(ROOT, 'shiftResponseHitTheta'):
        ROOT.gInterpreter.Declare('#include "SimDataFormats/TrackingHit/interface/PSimHit.h"\n'
            'double shiftResponseHitTheta(PSimHit const& h){return h.thetaAtEntry();}\n'
            'double shiftResponseHitPhi(PSimHit const& h){return h.phiAtEntry();}')
    xyz=lambda p:[float(p.x()),float(p.y()),float(p.z())]
    four=lambda p:xyz(p)+[float(p.t())]
    file=ROOT.TFile.Open(str(path))
    if not file or file.IsZombie() or file.TestBit(ROOT.TFile.kRecovered):
        raise ValueError('Bad native SIM file')
    try:
        names=[str(b.GetName()) for b in file.Get('Events').GetListOfBranches()]
    finally:
        file.Close()
    labels=sorted({n.split('_g4SimHits_',1)[1].rsplit('_',1)[0] for n in names
                   if n.startswith('PSimHits_g4SimHits_')})
    if not labels:
        raise ValueError('No native tracking hit collections')
    rows=[]
    for event in Events(str(path)):
        signature,muons,parents=graph(event)
        _,hepmc=product(event,'shiftEventTime','edm::HepMCProduct')
        children={int(p.barcode()):[int(c.barcode()) for c in items(
            p.end_vertex().particles_out_const_begin(),p.end_vertex().particles_out_const_end())]
            if p.end_vertex() else [] for p in items(
                hepmc.GetEvent().particles_begin(),hepmc.GetEvent().particles_end())}
        row=dict(event_id=list(identity(event)),graph_sha256=signature,
                 GEN_muons=muons,GEN_parents=parents,GEN_children=children)
        handle=Handle('std::vector<SimTrack>');event.getByLabel('g4SimHits',handle)
        if not handle.isValid():raise ValueError('Missing native SimTrack')
        row['tracks']=[dict(trackId=int(t.trackId()),pdg=int(t.type()),
            momentum=four(t.momentum()),vertex=int(t.vertIndex()),GEN_barcode=int(t.genpartIndex()),
            primary_last_stored_ID=int(t.getPrimaryOrLastStoredID()),track_info=int(t.getTrackInfo()),
            eventId=int(t.eventId().rawId()),tracker_position=xyz(t.trackerSurfacePosition()),
            tracker_momentum=four(t.trackerSurfaceMomentum()),crossed_boundary=bool(t.crossedBoundary()),
            id_at_boundary=int(t.getIDAtBoundary()),position_at_boundary=four(t.getPositionAtBoundary()),
            momentum_at_boundary=four(t.getMomentumAtBoundary())) for t in handle.product()]
        handle=Handle('std::vector<SimVertex>');event.getByLabel('g4SimHits',handle)
        if not handle.isValid():raise ValueError('Missing native SimVertex')
        row['vertices']=[dict(position=four(v.position()),parent=int(v.parentIndex()),
            vertex=int(v.vertexId()),process=int(v.processType()),eventId=int(v.eventId().rawId()))
            for v in handle.product()]
        hits={}
        for label in labels:
            handle=Handle('std::vector<PSimHit>');event.getByLabel('g4SimHits',label,handle)
            if not handle.isValid():
                hits[label]=None;continue
            hits[label]=[]
            for hit in handle.product():
                value=dict(entry=xyz(hit.entryPoint()),exit=xyz(hit.exitPoint()),p=float(hit.pabs()),
                    tof=float(hit.timeOfFlight()),energyLoss=float(hit.energyLoss()),
                    theta=float(ROOT.shiftResponseHitTheta(hit)),phi=float(ROOT.shiftResponseHitPhi(hit)),
                    pdg=int(hit.particleType()),detector=int(hit.detUnitId()),track=int(hit.trackId()),
                    eventId=int(hit.eventId().rawId()),process=int(hit.processType()),hitProd=int(hit.hitProdType()))
                if label=='MuonCSCHits':
                    detector=ROOT.CSCDetId(value['detector'])
                    value.update(csc_endcap=int(detector.endcap()),csc_station=int(detector.station()))
                hits[label].append(value)
        row['tracking_hit_collections']=hits;rows.append(row)
    return dict(metadata=metadata(path),events=rows)


def parse_logger(log):
    """Preserve every structured passive row and actual native delegate path."""
    result=dict(decays=[],terminals=[],delegates=[],errors=[])
    for line in log.splitlines():
        for marker,key in [('[SHIFTParentDecay] ','decays'),('[SHIFTParentTerminal] ','terminals')]:
            if marker in line:
                result[key].append(json.loads(line.split(marker,1)[1]))
        marker='[SHIFTParentLoggerDelegate] '
        if marker in line:
            symbol,path=line.split(marker,1)[1].split('=',1)
            result['delegates'].append(dict(symbol=symbol,path=path))
        if '[SHIFTParentLoggerError]' in line:
            result['errors'].append(line)
    return result


def _ancestor_tracks(track,tracks,vertices):
    ancestry=[];missing=[];seen={(track.get('eventId',0),track['trackId'])}
    while 0<=track['vertex']<len(vertices):
        vertex=vertices[track['vertex']];number=vertex['parent']
        if number<0:break
        key=(vertex.get('eventId',track.get('eventId',0)),number)
        if key in seen:raise ValueError('Cycle in saved G4 track ancestry')
        seen.add(key)
        if key not in tracks:
            missing.append(dict(eventId=key[0],trackId=number));break
        track=tracks[key]
        ancestry.append(dict(trackId=number,eventId=key[0],pdg=track['pdg'],GEN_barcode=track['GEN_barcode']))
    return ancestry,missing


def _conservation(row):
    parent=row['parent_entry_p4_GeV'];children=row['children']
    if len(parent)!=4 or not children or not all(math.isfinite(v) for v in parent):
        raise ValueError('Incomplete observed parent decay')
    if any(len(c['p4_GeV'])!=4 or not all(math.isfinite(v) for v in c['p4_GeV']) for c in children):
        raise ValueError('Incomplete observed daughter four-vector')
    residual=max(abs(parent[i]-math.fsum(c['p4_GeV'][i] for c in children)) for i in range(4))
    limit=1.e-7*max(1.,abs(parent[3]))
    return dict(trackId=row['trackId'],barcode=row['barcode'],residual_GeV=residual,
        diagnostic_limit_GeV=limit,within_qualified_diagnostic_limit=residual<=limit,
        diagnostic_only=True,physical_selection_or_repair_applied=False)


def summarize_selected(native,logs,selected_muon_barcodes,parent_barcodes,
                       reco_labels=None,reco_truth=None,expected_event_id=None):
    """Connect one exact original through actual transport, CSC hits and reco.

    A selected parent's planned GEN decay does not establish its actual fate.
    Pileup event IDs remain part of native hit/track keys. Reco associations with
    only a G4 ID are marked ambiguous when that ID belongs to several events.
    """
    if len(native['events'])!=1:
        raise ValueError('Selected response audit requires one exact original')
    event=native['events'][0];logs=parse_logger(logs) if isinstance(logs,str) else logs
    identity=event['event_id']
    if (len(identity)!=3 or any(type(v) is not int or not 0<=v<2**bits
                               for v,bits in zip(identity,[32,32,64]))):
        raise ValueError('Native snapshot has an invalid complete event identity')
    if expected_event_id is not None and list(expected_event_id)!=identity:
        raise ValueError('Native complete event identity differs from its frozen request')
    label_rows=[] if reco_labels is None else [reco_labels] if isinstance(reco_labels,dict) else reco_labels
    if any(row.get('event_id')!=identity for row in label_rows):
        raise ValueError('Reco labels belong to another complete original identity')
    # RunManagerMTWorker::generateEvent creates G4Event((G4int)inpevt.id().event()).
    # G4Types.hh defines G4int=int; the frozen el9 x86_64/GCC runtime uses signed
    # 32-bit int. This is the EDM event NUMBER, not an ordinal or SimTrack.eventId.
    if ctypes.sizeof(ctypes.c_int)!=4:
        raise ValueError('Native G4int event conversion is unqualified on this architecture')
    expected_g4=ctypes.c_int(identity[2]).value
    tracks={(t.get('eventId',0),t['trackId']):t for t in event['tracks']}
    if len(tracks)!=len(event['tracks']):raise ValueError('Duplicate saved native track identity')
    vertices=event['vertices'];hits=event['tracking_hit_collections'].get('MuonCSCHits')
    observed_hits=hits is not None;hits=hits or []
    decays=logs['decays'];terminals=logs['terminals']
    if any(type(d.get('g4_event')) is not int or d['g4_event']!=expected_g4 for d in decays+terminals):
        raise ValueError('Passive log contains another original event')
    fields=(reco_truth or {}).get('stored_fields',reco_truth or {})
    reco_ids=fields.get('ShiftMuon_hitSimTrackId',[])
    muons=[]
    for barcode in selected_muon_barcodes:
        matched={(t.get('eventId',0),t['trackId']):t for t in tracks.values()
                 if t['GEN_barcode']==barcode and abs(t['pdg'])==13}
        evidence={key:[dict(kind='native_GEN_barcode',barcode=barcode)] for key in matched}
        terminal=[t for t in terminals if t['barcode']==barcode and abs(t['pdg'])==13]
        ambiguities=[]
        for record in terminal:
            candidates=[(key,t) for key,t in tracks.items() if t['trackId']==record['trackId']
                        and ('eventId' not in record or key[0]==record['eventId'])]
            if len(candidates)!=1:
                ambiguities.append(dict(reason='passive_G4_ID_has_no_unique_saved_event_track',
                    trackId=record['trackId'],native_candidates=[dict(eventId=k[0],trackId=k[1])
                        for k,_ in candidates],passive_record=record))
                continue
            key,track=candidates[0]
            if track['pdg']!=record['pdg'] or track['GEN_barcode']>0 and track['GEN_barcode']!=barcode:
                ambiguities.append(dict(reason='passive_native_PDG_or_GEN_barcode_conflict',
                    trackId=record['trackId'],eventId=key[0],native_GEN_barcode=track['GEN_barcode'],
                    native_pdg=track['pdg'],passive_record=record))
                continue
            matched[key]=track
            evidence.setdefault(key,[]).append(dict(kind='passive_barcode_and_exact_G4_track_ID',
                barcode=barcode,g4_event=record['g4_event'],trackId=record['trackId'],
                native_eventId=key[0],native_eventId_explicit_in_passive_record='eventId' in record,
                passive_step=record['step']))
        matches=list(matched.values())
        states=[]
        for track in matches:
            key=(track.get('eventId',0),track['trackId'])
            owned=[h for h in hits if (h.get('eventId',0),h['track'])==key]
            stations={side:dict(Counter(h['csc_station'] for h in owned if h['csc_endcap']==number))
                      for side,number in [('plus',1),('minus',2)]}
            ancestry,missing=_ancestor_tracks(track,tracks,vertices)
            same_id=[t for t in tracks.values() if t['trackId']==track['trackId']]
            states.append(dict(trackId=track['trackId'],eventId=key[0],momentum=track['momentum'],
                native_GEN_barcode=track['GEN_barcode'],association_evidence=evidence[key],
                birth_vertex=vertices[track['vertex']] if 0<=track['vertex']<len(vertices) else None,
                saved_parent_chain=ancestry,missing_saved_parent_tracks=missing,
                CSC_hits=len(owned),CSC_station_hits=stations,CSC_collection_present=observed_hits,
                reco_muon_indices=[i for i,value in enumerate(reco_ids) if value==track['trackId']]
                    if len(same_id)==1 else [],reco_track_ID_is_ambiguous=len(same_id)!=1))
        births=[d for d in decays if any(c['barcode']==barcode and abs(c['pdg'])==13 for c in d['children'])]
        muons.append(dict(barcode=barcode,saved_tracks=states,actual_parent_decay_records=births,
            actual_first_or_terminal_records=terminal,
            native_track_association_ambiguities=ambiguities,
            actual_birth_observed=bool(matches or births or any(t['step']==1 for t in terminal)),
            missing_saved_track_is_unknown=not bool(matches)))
    parents=[]
    for barcode in parent_barcodes:
        actual=[d for d in decays if d['barcode']==barcode]
        terminal=[t for t in terminals if t['barcode']==barcode]
        stopped=[t for t in terminal if t['process_subtype']==121
                 and t['status_before_original_action']==2
                 and t['assigned_proper_time_ns']>t['proper_time_ns']>=0.]
        contradictions=[dict(stop=t,decays=[d for d in actual if d['trackId']==t['trackId']])
                        for t in stopped if any(d['trackId']==t['trackId'] for d in actual)]
        children=event.get('GEN_children',{});descendants=set();pending=[barcode]
        while pending:
            number=pending.pop()
            for child in children.get(number,children.get(str(number),[])):
                if child not in descendants:
                    descendants.add(child);pending.append(child)
        selected_descendants=[m for m in muons if m['barcode'] in descendants]
        unexpected=[dict(barcode=m['barcode'],saved_tracks=m['saved_tracks'],
                         actual_decay_records=m['actual_parent_decay_records'])
                    for m in selected_descendants if stopped and m['actual_birth_observed']]
        roots=[dict(barcode=m['barcode'],trackId=t['trackId']) for m in selected_descendants
               for t in m['saved_tracks'] if stopped and t['birth_vertex'] is not None
               and t['birth_vertex']['parent']<0]
        parents.append(dict(barcode=barcode,actual_decays=actual,actual_first_or_terminal_records=terminal,
            inelastic_termination_before_assigned_decay=stopped,
            stopped_parent_with_observed_decay=contradictions,
            GEN_selected_descendant_muon_barcodes=[m['barcode'] for m in selected_descendants],
            selected_descendant_births_despite_early_termination=unexpected,
            selected_descendants_injected_as_roots_despite_early_termination=roots,
            observed_fate='inelastic_termination_observed' if stopped else
                          'decay_observed' if actual else 'unknown',
            missing_record_is_not_absorption=not bool(stopped or actual)))
    checks=[_conservation(d) for d in decays]
    return dict(schema='shift-selected-transport-response-v1',event_id=event['event_id'],
        event_identity_binding=dict(native_complete_event_id=identity,
            expected_complete_event_id=list(expected_event_id) if expected_event_id is not None else None,
            reco_complete_identities_checked=len(label_rows),expected_g4_event_id=expected_g4,
            g4_event_mapping='CMSSW RunManagerMTWorker::generateEvent: signed32(EDM event number)',
            g4_event_alone_does_not_encode_run_lumi_or_high_event_bits=True),
        selected_muons=muons,selected_parents=parents,decay_conservation=checks,
        native_CSC_hit_tracks_missing_from_saved_tracks=[dict(eventId=e,trackId=t)
            for e,t in sorted({(h.get('eventId',0),h['track']) for h in hits}-set(tracks))],
        reco_labels=reco_labels,reco_truth=reco_truth,native_delegates=logs['delegates'],
        logger_errors=logs['errors'],missing_evidence_is_unknown=True,
        observed_decays_pass_diagnostic_conservation=all(c['within_qualified_diagnostic_limit'] for c in checks)
            if checks else None,applies_no_physics_weight_or_selection=True,
        representative_efficiency_or_rate=False)
