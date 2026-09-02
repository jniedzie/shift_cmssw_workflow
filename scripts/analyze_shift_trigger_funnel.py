#!/usr/bin/env python3
"""Audit the fixed Run-3 muon-trigger funnel after SHIFT detector digitization.

CSC LCTs are compared across the actual pack/unpack boundary.  DT trigger
primitives and regional/uGMT candidates are retained as explicitly weaker,
pre-RAW diagnostics because the standard unpacked file has no corresponding
DT primitive product and downstream candidates have no SimTrack association.
"""

import argparse
import json
from collections import Counter, defaultdict

import ROOT
from DataFormats.FWLite import Events, Handle

from audit_shift_muon_truth import _audit_event


ROOT.gInterpreter.Declare(
    r"""
#include <cmath>
#include <cstdint>
#include <vector>

#include "DataFormats/CSCDigi/interface/CSCCorrelatedLCTDigiCollection.h"
#include "DataFormats/L1DTTrackFinder/interface/L1MuDTChambPhContainer.h"
#include "DataFormats/L1TMuon/interface/RegionalMuonCand.h"
#include "DataFormats/L1Trigger/interface/Muon.h"
#include "DataFormats/MuonDetId/interface/CSCDetId.h"
#include "DataFormats/MuonDetId/interface/DTWireId.h"
#include "SimDataFormats/TrackingHit/interface/PSimHit.h"
#include "SimDataFormats/TrackingHit/interface/PSimHitContainer.h"

struct ShiftSignalChamber {
  uint32_t eventId;
  uint32_t trackId;
  int subsystem;
  uint32_t chamberId;
  int wheel;
  int station;
  int sector;
};

struct ShiftCSCLCT {
  uint32_t chamberId;
  int bx;
  int trackNumber;
  int quality;
  int keyWire;
  int strip;
  int pattern;
  int bend;
  int run3Pattern;
  int slope;
};

struct ShiftDTPrimitive {
  int bx;
  int wheel;
  int station;
  int sector;
  int phi;
  int phiB;
  int quality;
};

struct ShiftBXCount {
  int bx;
  int count;
};

std::vector<ShiftSignalChamber> shiftSignalCSCChambers(edm::PSimHitContainer const& hits) {
  std::vector<ShiftSignalChamber> result;
  for (auto const& hit : hits) {
    if (std::abs(hit.particleType()) != 13)
      continue;
    CSCDetId chamber = CSCDetId(hit.detUnitId()).chamberId();
    result.push_back({hit.eventId().rawId(), hit.trackId(), 1, chamber.rawId(), 0, 0, 0});
  }
  return result;
}

std::vector<ShiftSignalChamber> shiftSignalDTChambers(edm::PSimHitContainer const& hits) {
  std::vector<ShiftSignalChamber> result;
  for (auto const& hit : hits) {
    if (std::abs(hit.particleType()) != 13)
      continue;
    DTChamberId chamber = DTWireId(hit.detUnitId()).chamberId();
    result.push_back({hit.eventId().rawId(), hit.trackId(), 0, chamber.rawId(),
                      chamber.wheel(), chamber.station(), chamber.sector()});
  }
  return result;
}

std::vector<ShiftCSCLCT> shiftFlattenCSCLCTs(CSCCorrelatedLCTDigiCollection const& digis) {
  std::vector<ShiftCSCLCT> result;
  for (auto const& detset : digis) {
    CSCDetId chamber = detset.first.chamberId();
    for (auto digi = detset.second.first; digi != detset.second.second; ++digi)
      result.push_back({chamber.rawId(), static_cast<int>(digi->getBX()),
                        static_cast<int>(digi->getTrknmb()), static_cast<int>(digi->getQuality()),
                        static_cast<int>(digi->getKeyWG()), static_cast<int>(digi->getStrip()),
                        static_cast<int>(digi->getPattern()), static_cast<int>(digi->getBend()),
                        static_cast<int>(digi->getRun3Pattern()), static_cast<int>(digi->getSlopeEx())});
  }
  return result;
}

std::vector<ShiftDTPrimitive> shiftFlattenDTPrimitives(L1MuDTChambPhContainer const& container) {
  std::vector<ShiftDTPrimitive> result;
  for (auto const& digi : *container.getContainer())
    result.push_back({digi.bxNum(), digi.whNum(), digi.stNum(), digi.scNum(),
                      digi.phi(), digi.phiB(), digi.code()});
  return result;
}

std::vector<ShiftBXCount> shiftRegionalBXCounts(l1t::RegionalMuonCandBxCollection const& cands) {
  std::vector<ShiftBXCount> result;
  for (int bx = cands.getFirstBX(); bx <= cands.getLastBX(); ++bx)
    result.push_back({bx, static_cast<int>(cands.size(bx))});
  return result;
}

std::vector<ShiftBXCount> shiftGlobalBXCounts(l1t::MuonBxCollection const& cands) {
  std::vector<ShiftBXCount> result;
  for (int bx = cands.getFirstBX(); bx <= cands.getLastBX(); ++bx)
    result.push_back({bx, static_cast<int>(cands.size(bx))});
  return result;
}
"""
)


REGIONAL_PRODUCTS = (
    ("BMTF", "simBmtfDigis", "BMTF"),
    ("KBMTF", "simKBmtfDigis", "BMTF"),
    ("OMTF", "simOmtfDigis", "OMTF"),
    ("EMTF", "simEmtfDigis", "EMTF"),
)


def _product(event, type_name, module, instance, process):
    handle = Handle(type_name)
    if not event.getByLabel(module, instance, process, handle):
        raise RuntimeError(f"missing {module}:{instance}:{process} ({type_name})")
    return handle.product()


def _optional_scalar(event, type_name, module, instance, process=None):
    handle = Handle(type_name)
    if process:
        found = event.getByLabel(module, instance, process, handle)
    else:
        found = event.getByLabel(module, instance, handle)
    if not found:
        return None
    product = handle.product()
    return product if type_name == "std::string" else product[0]


def _timing_provenance(event, process):
    for module, timing_kind, product_process in (
        ("shiftSimHitTime", "post-Geant4 same-SimHit reference", process),
        ("shiftEventTime", "physical event time before Geant4", None),
    ):
        bx_offset = _optional_scalar(
            event, "int", module, "bxOffset", product_process
        )
        if bx_offset is None:
            continue
        return {
            "source_module": module,
            "timing_kind": timing_kind,
            "bx_offset": int(bx_offset),
            "phase_ns": float(
                _optional_scalar(event, "double", module, "phaseNs", product_process)
            ),
            "applied_shift_ns": float(
                _optional_scalar(
                    event, "double", module, "appliedShiftNs", product_process
                )
            ),
            "model_version": str(
                _optional_scalar(
                    event, "std::string", module, "modelVersion", product_process
                )
            ),
        }
    return None


def _truth_chambers(event, process):
    result = defaultdict(lambda: {"CSC": set(), "DT": set()})
    for subsystem, instance, converter in (
        ("CSC", "MuonCSCHits", ROOT.shiftSignalCSCChambers),
        ("DT", "MuonDTHits", ROOT.shiftSignalDTChambers),
    ):
        hits = _product(event, "std::vector<PSimHit>", "g4SimHits", instance, process)
        for item in converter(hits):
            key = int(item.eventId), int(item.trackId)
            if subsystem == "CSC":
                result[key][subsystem].add(int(item.chamberId))
            else:
                result[key][subsystem].add(
                    (int(item.wheel), int(item.station), int(item.sector))
                )
    return result


def _lct_key(item, bx_offset=0):
    return (
        int(item.chamberId), int(item.bx) + bx_offset, int(item.trackNumber),
        int(item.quality), int(item.keyWire), int(item.strip), int(item.bend),
    )


def _lct_record(key):
    names = (
        "chamber_id", "readout_relative_bx", "track_number", "quality", "key_wire",
        "strip", "bend",
    )
    return dict(zip(names, key))


def _lct_payload(item):
    return int(item.pattern), int(item.run3Pattern), int(item.slope)


def _lct_map(items, bx_offset=0):
    result = {}
    for item in items:
        key = _lct_key(item, bx_offset)
        if key in result:
            raise RuntimeError(f"duplicate canonical CSC LCT identity: {key}")
        result[key] = _lct_payload(item)
    return result


def _bx_counts(event, process):
    result = {}
    for name, module, instance in REGIONAL_PRODUCTS:
        product = _product(
            event, "BXVector<l1t::RegionalMuonCand>", module, instance, process
        )
        result[name] = {
            str(int(item.bx)): int(item.count)
            for item in ROOT.shiftRegionalBXCounts(product)
            if int(item.count)
        }
    ugmt = _product(event, "BXVector<l1t::Muon>", "simGmtStage2Digis", "", process)
    result["uGMT"] = {
        str(int(item.bx)): int(item.count)
        for item in ROOT.shiftGlobalBXCounts(ugmt)
        if int(item.count)
    }
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Audit fixed Run-3 local/regional muon trigger products for SHIFT events."
    )
    parser.add_argument("input", help="diagnostic output of shift_readout_unpack_cfg.py")
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-events", type=int, default=-1)
    parser.add_argument("--input-process", default="HLT")
    parser.add_argument("--truth-process", default="SIM")
    parser.add_argument("--readout-process", default="SHIFTREADOUT")
    args = parser.parse_args()

    output = {
        "schema_version": 1,
        "input": args.input,
        "validated_boundary": (
            "signal-muon chamber compatibility -> simulated CSC correlated LCT -> "
            "packed RAW -> standard-unpacked CSC correlated LCT"
        ),
        "limitations": [
            "Chamber compatibility is not an exact SimTrack-to-trigger-primitive association.",
            "DT trigger primitives have no post-RAW product in this standard unpack chain.",
            "Regional and uGMT counts are event-global and are not assigned to a signal muon.",
            "A trigger object is not proof that the event was accepted or recorded by HLT/DAQ.",
        ],
        "events": [],
        "simhit_reference_timing": None,
    }

    summary = Counter()
    for index, event in enumerate(Events(args.input)):
        if args.max_events >= 0 and index >= args.max_events:
            break
        truth = _audit_event(event, args.input)
        timing = _timing_provenance(event, args.input_process)
        timing_configuration = (
            {key: value for key, value in timing.items() if key != "applied_shift_ns"}
            if timing is not None
            else None
        )
        if output["simhit_reference_timing"] is None:
            output["simhit_reference_timing"] = timing_configuration
        elif timing_configuration != output["simhit_reference_timing"]:
            raise RuntimeError("SHIFT timing provenance changes between events")
        chambers = _truth_chambers(event, args.truth_process)

        prepack_product = _product(
            event,
            "MuonDigiCollection<CSCDetId,CSCCorrelatedLCTDigi>",
            "simCscTriggerPrimitiveDigis", "", args.input_process,
        )
        unpacked_product = _product(
            event,
            "MuonDigiCollection<CSCDetId,CSCCorrelatedLCTDigi>",
            "muonCSCDigis", "MuonCSCCorrelatedLCTDigi", args.readout_process,
        )
        prepack_items = list(ROOT.shiftFlattenCSCLCTs(prepack_product))
        unpacked_items = list(ROOT.shiftFlattenCSCLCTs(unpacked_product))
        # CMSSW CSCDigiValidator compares correlated LCTs using emulator BX - 6.
        # This converts representations for comparison; it does not retime anything.
        prepack_lcts = _lct_map(prepack_items, -6)
        unpacked_lcts = _lct_map(unpacked_items)

        dt_product = _product(
            event, "L1MuDTChambPhContainer", "simDtTriggerPrimitiveDigis", "", args.input_process
        )
        dt_primitives = list(ROOT.shiftFlattenDTPrimitives(dt_product))

        event_result = {
            "run": truth["run"], "lumi": truth["lumi"], "event": truth["event"],
            "applied_shift_ns": (
                float(timing["applied_shift_ns"]) if timing is not None else None
            ),
            "regional_candidate_bx_counts": _bx_counts(event, args.input_process),
            "signal_muons": [],
        }
        for muon in truth["signal_muons"]:
            truth_key = (muon["event_id"]["raw"], muon["track_id"])
            csc_chambers = chambers[truth_key]["CSC"]
            compatible_prepack = {key for key in prepack_lcts if key[0] in csc_chambers}
            compatible_unpacked = {key for key in unpacked_lcts if key[0] in csc_chambers}
            matched = compatible_prepack & compatible_unpacked
            missing = compatible_prepack - compatible_unpacked
            payload_changes = [
                {
                    "primitive": _lct_record(key),
                    "prepack_pattern_run3_pattern_slope": list(prepack_lcts[key]),
                    "unpacked_pattern_run3_pattern_slope": list(unpacked_lcts[key]),
                }
                for key in sorted(matched)
                if prepack_lcts[key] != unpacked_lcts[key]
            ]

            dt_chambers = chambers[truth_key]["DT"]
            # DT trigger sectors are conventionally zero-based.  Keep both the
            # original value and a chamber-compatible selection using sector+1.
            compatible_dt = [
                item for item in dt_primitives
                if (int(item.wheel), int(item.station), int(item.sector) + 1) in dt_chambers
            ]
            csc_status = "not_crossed"
            if csc_chambers:
                if not compatible_prepack:
                    csc_status = "no_chamber_compatible_simulated_LCT"
                elif not missing and payload_changes:
                    csc_status = "all_chamber_compatible_LCTs_unpacked_with_payload_changes"
                elif not missing:
                    csc_status = "all_chamber_compatible_LCTs_unpacked_from_RAW"
                elif matched:
                    csc_status = "partial_chamber_compatible_LCT_RAW_closure"
                else:
                    csc_status = "no_chamber_compatible_LCTs_unpacked_from_RAW"
            summary[csc_status] += 1

            event_result["signal_muons"].append({
                "event_id": muon["event_id"], "track_id": muon["track_id"],
                "pdg_id": muon["pdg_id"],
                "CSC": {
                    "crossed_chambers": sorted(csc_chambers),
                    "compatible_prepack_LCTs": [_lct_record(key) for key in sorted(compatible_prepack)],
                    "matched_unpacked_LCTs": [_lct_record(key) for key in sorted(matched)],
                    "missing_unpacked_LCTs": [_lct_record(key) for key in sorted(missing)],
                    "payload_changes_after_unpack": payload_changes,
                    "unpacked_without_exact_prepack_match": [
                        _lct_record(key) for key in sorted(compatible_unpacked - compatible_prepack)
                    ],
                    "status": csc_status,
                },
                "DT": {
                    "crossed_chambers": [list(chamber) for chamber in sorted(dt_chambers)],
                    "compatible_simulated_primitives": [
                        {"bx": int(item.bx), "wheel": int(item.wheel),
                         "station": int(item.station), "sector_zero_based": int(item.sector),
                         "phi": int(item.phi), "phi_b": int(item.phiB),
                         "quality": int(item.quality)}
                        for item in compatible_dt
                    ],
                    "status": (
                        "simulated_only_no_standard_RAW_unpacked_DT_primitive"
                        if compatible_dt
                        else "no_chamber_compatible_simulated_primitive"
                    ) if dt_chambers else "not_crossed",
                },
            })
        output["events"].append(event_result)

    output["summary"] = {
        "events": len(output["events"]),
        "signal_muons": sum(len(event["signal_muons"]) for event in output["events"]),
        "CSC_statuses": dict(sorted(summary.items())),
        "events_with_regional_or_uGMT_candidates": sum(
            any(counts for counts in event["regional_candidate_bx_counts"].values())
            for event in output["events"]
        ),
    }
    with open(args.output, "w", encoding="utf-8") as output_file:
        json.dump(output, output_file, indent=2, sort_keys=True)
        output_file.write("\n")
    print(json.dumps(output["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
