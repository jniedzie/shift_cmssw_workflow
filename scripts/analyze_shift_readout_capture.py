#!/usr/bin/env python3
"""Measure truth-linked SHIFT digi survival through one packed RAW readout."""

import argparse
import json
from collections import defaultdict

import ROOT
from DataFormats.FWLite import Events, Handle

from audit_shift_muon_truth import SUBDETECTORS, _audit_event


ROOT.gInterpreter.Declare(
    r"""
#include <cstdint>
#include <vector>

#include "DataFormats/Common/interface/DetSetVector.h"
#include "DataFormats/CSCDigi/interface/CSCStripDigiCollection.h"
#include "DataFormats/CSCDigi/interface/CSCWireDigiCollection.h"
#include "DataFormats/DTDigi/interface/DTDigiCollection.h"
#include "DataFormats/GEMDigi/interface/GEMDigiCollection.h"
#include "DataFormats/RPCDigi/interface/RPCDigiCollection.h"
#include "SimDataFormats/DigiSimLinks/interface/DTDigiSimLinkCollection.h"
#include "SimDataFormats/GEMDigiSimLink/interface/GEMDigiSimLink.h"
#include "SimDataFormats/RPCDigiSimLink/interface/RPCDigiSimLink.h"
#include "SimDataFormats/TrackerDigiSimLink/interface/StripDigiSimLink.h"

struct ShiftFlatLink {
  int kind;
  uint32_t detId;
  int channel;
  int sample;
  uint32_t eventId;
  uint32_t trackId;
};

struct ShiftFlatDigi {
  int kind;
  uint32_t detId;
  int channel;
  int sample;
};

enum ShiftReadoutKind { kDT = 0, kCSCStrip = 1, kCSCWire = 2, kRPC = 3, kGEM = 4 };

std::vector<ShiftFlatLink> shiftFlattenDTLinks(DTDigiSimLinkCollection const& links) {
  std::vector<ShiftFlatLink> result;
  for (auto const& detset : links)
    for (auto link = detset.second.first; link != detset.second.second; ++link)
      result.push_back({kDT,
                        detset.first.rawId(),
                        link->wire(),
                        static_cast<int>(link->countsTDC()),
                        link->eventId().rawId(),
                        link->SimTrackId()});
  return result;
}

std::vector<ShiftFlatLink> shiftFlattenCSCLinks(edm::DetSetVector<StripDigiSimLink> const& links, int kind) {
  std::vector<ShiftFlatLink> result;
  for (auto const& detset : links)
    for (auto const& link : detset)
      result.push_back(
          {kind, detset.id, static_cast<int>(link.channel()), -1, link.eventId().rawId(), link.SimTrackId()});
  return result;
}

std::vector<ShiftFlatLink> shiftFlattenRPCLinks(edm::DetSetVector<RPCDigiSimLink> const& links) {
  std::vector<ShiftFlatLink> result;
  for (auto const& detset : links)
    for (auto const& link : detset)
      result.push_back({kRPC,
                        detset.id,
                        static_cast<int>(link.getStrip()),
                        static_cast<int>(link.getBx()),
                        link.getEventId().rawId(),
                        link.getTrackId()});
  return result;
}

std::vector<ShiftFlatLink> shiftFlattenGEMLinks(edm::DetSetVector<GEMDigiSimLink> const& links) {
  std::vector<ShiftFlatLink> result;
  for (auto const& detset : links)
    for (auto const& link : detset)
      result.push_back({kGEM,
                        detset.id,
                        static_cast<int>(link.getStrip()),
                        link.getBx(),
                        link.getEventId().rawId(),
                        link.getTrackId()});
  return result;
}

std::vector<ShiftFlatDigi> shiftFlattenDTDigis(DTDigiCollection const& digis) {
  std::vector<ShiftFlatDigi> result;
  for (auto const& detset : digis)
    for (auto digi = detset.second.first; digi != detset.second.second; ++digi)
      result.push_back({kDT, detset.first.rawId(), digi->wire(), digi->countsTDC()});
  return result;
}

std::vector<ShiftFlatDigi> shiftFlattenCSCStripDigis(CSCStripDigiCollection const& digis) {
  std::vector<ShiftFlatDigi> result;
  for (auto const& detset : digis)
    for (auto digi = detset.second.first; digi != detset.second.second; ++digi)
      result.push_back({kCSCStrip, detset.first.rawId(), digi->getStrip(), -1});
  return result;
}

std::vector<ShiftFlatDigi> shiftFlattenCSCWireDigis(CSCWireDigiCollection const& digis) {
  std::vector<ShiftFlatDigi> result;
  for (auto const& detset : digis)
    for (auto digi = detset.second.first; digi != detset.second.second; ++digi)
      result.push_back({kCSCWire, detset.first.rawId(), digi->getWireGroup(), -1});
  return result;
}

std::vector<ShiftFlatDigi> shiftFlattenRPCDigis(RPCDigiCollection const& digis) {
  std::vector<ShiftFlatDigi> result;
  for (auto const& detset : digis)
    for (auto digi = detset.second.first; digi != detset.second.second; ++digi)
      result.push_back({kRPC, detset.first.rawId(), digi->strip(), digi->bx()});
  return result;
}

std::vector<ShiftFlatDigi> shiftFlattenGEMDigis(GEMDigiCollection const& digis) {
  std::vector<ShiftFlatDigi> result;
  for (auto const& detset : digis)
    for (auto digi = detset.second.first; digi != detset.second.second; ++digi)
      result.push_back({kGEM, detset.first.rawId(), digi->strip(), digi->bx()});
  return result;
}
"""
)


LINK_PRODUCTS = (
    (
        "std::vector<ShiftFlatLink>",
        "MuonDigiCollection<DTLayerId,DTDigiSimLink>",
        "simMuonDTDigis",
        "",
        ROOT.shiftFlattenDTLinks,
        (),
    ),
    (
        "std::vector<ShiftFlatLink>",
        "edm::DetSetVector<StripDigiSimLink>",
        "simMuonCSCDigis",
        "MuonCSCStripDigiSimLinks",
        ROOT.shiftFlattenCSCLinks,
        (1,),
    ),
    (
        "std::vector<ShiftFlatLink>",
        "edm::DetSetVector<StripDigiSimLink>",
        "simMuonCSCDigis",
        "MuonCSCWireDigiSimLinks",
        ROOT.shiftFlattenCSCLinks,
        (2,),
    ),
    (
        "std::vector<ShiftFlatLink>",
        "edm::DetSetVector<RPCDigiSimLink>",
        "simMuonRPCDigis",
        "RPCDigiSimLink",
        ROOT.shiftFlattenRPCLinks,
        (),
    ),
    (
        "std::vector<ShiftFlatLink>",
        "edm::DetSetVector<GEMDigiSimLink>",
        "simMuonGEMDigis",
        "GEM",
        ROOT.shiftFlattenGEMLinks,
        (),
    ),
)

DIGI_PRODUCTS = (
    ("MuonDigiCollection<DTLayerId,DTDigi>", "muonDTDigis", "", ROOT.shiftFlattenDTDigis),
    (
        "MuonDigiCollection<CSCDetId,CSCStripDigi>",
        "muonCSCDigis",
        "MuonCSCStripDigi",
        ROOT.shiftFlattenCSCStripDigis,
    ),
    (
        "MuonDigiCollection<CSCDetId,CSCWireDigi>",
        "muonCSCDigis",
        "MuonCSCWireDigi",
        ROOT.shiftFlattenCSCWireDigis,
    ),
    ("MuonDigiCollection<RPCDetId,RPCDigi>", "muonRPCDigis", "", ROOT.shiftFlattenRPCDigis),
    ("MuonDigiCollection<GEMDetId,GEMDigi>", "muonGEMDigis", "", ROOT.shiftFlattenGEMDigis),
)

PREPACK_DIGI_PRODUCTS = (
    ("MuonDigiCollection<DTLayerId,DTDigi>", "simMuonDTDigis", "", ROOT.shiftFlattenDTDigis),
    (
        "MuonDigiCollection<CSCDetId,CSCStripDigi>",
        "simMuonCSCDigis",
        "MuonCSCStripDigi",
        ROOT.shiftFlattenCSCStripDigis,
    ),
    (
        "MuonDigiCollection<CSCDetId,CSCWireDigi>",
        "simMuonCSCDigis",
        "MuonCSCWireDigi",
        ROOT.shiftFlattenCSCWireDigis,
    ),
    ("MuonDigiCollection<RPCDetId,RPCDigi>", "simMuonRPCDigis", "", ROOT.shiftFlattenRPCDigis),
    ("MuonDigiCollection<GEMDetId,GEMDigi>", "simMuonGEMDigis", "", ROOT.shiftFlattenGEMDigis),
)

KIND_SUBSYSTEM = {0: "DT", 1: "CSC", 2: "CSC", 3: "RPC", 4: "GEM"}


def _product(event, type_name, module, instance, process):
    handle = Handle(type_name)
    if not event.getByLabel(module, instance, process, handle):
        raise RuntimeError(f"missing {module}:{instance}:{process} ({type_name})")
    return handle.product()


def _digi_key(item):
    return int(item.kind), int(item.detId), int(item.channel), int(item.sample)


def _channel_key(digi_key):
    kind, det_id, channel, _ = digi_key
    return [kind, det_id, channel]


def _flatten_links(event, input_process):
    links = []
    for _, type_name, module, instance, converter, extra in LINK_PRODUCTS:
        product = _product(event, type_name, module, instance, input_process)
        links.extend(converter(product, *extra))
    return links


def _flatten_digis(event, process, products):
    digis = []
    for type_name, module, instance, converter in products:
        product = _product(event, type_name, module, instance, process)
        digis.extend(converter(product))
    return digis


def _subsystem_status(simhits, linked, matched):
    if simhits == 0:
        return "not_crossed"
    if linked == 0:
        return "lost_before_or_at_digitization"
    if matched == linked:
        return "stored_in_this_readout"
    if matched:
        return "partial_between_digitization_and_unpacked_RAW"
    return "not_stored_between_digitization_and_unpacked_RAW"


def _overall_classification(subsystems):
    crossed = [value for value in subsystems.values() if value["simhits"]]
    if not crossed:
        return "no_muon_detector_crossing"
    if all(value["status"] == "stored_in_this_readout" for value in crossed):
        return "complete_at_digi_RAW_boundary"
    if any(value["matched_unpacked_digis"] for value in crossed):
        return "partial_at_digi_RAW_boundary"
    return "no_muon_content_in_this_readout"


def main():
    parser = argparse.ArgumentParser(
        description="Classify truth-linked SHIFT digi survival through one unpacked RAW readout."
    )
    parser.add_argument("input", help="output of shift_readout_unpack_cfg.py")
    parser.add_argument("--output", required=True, help="output JSON path")
    parser.add_argument("--max-events", type=int, default=-1)
    parser.add_argument("--input-process", default="HLT")
    parser.add_argument("--readout-process", default="SHIFTREADOUT")
    args = parser.parse_args()

    output = {
        "schema_version": 1,
        "input": args.input,
        "validated_boundary": "truth SimHit -> simulated digi/link -> packed RAW -> unpacked digi",
        "limitations": [
            "This classifies one triggered CMS readout at one physical timing point.",
            "CSC strip/wire closure is channel-level; time-bin comparison is pending.",
            "Regional trigger, uGMT, HLT object, and offline reconstruction closure are pending.",
        ],
        "events": [],
    }

    for index, event in enumerate(Events(args.input)):
        if args.max_events >= 0 and index >= args.max_events:
            break
        truth = _audit_event(event, args.input)
        links = _flatten_links(event, args.input_process)
        prepack = {
            _digi_key(digi) for digi in _flatten_digis(event, args.input_process, PREPACK_DIGI_PRODUCTS)
        }
        unpacked = {_digi_key(digi) for digi in _flatten_digis(event, args.readout_process, DIGI_PRODUCTS)}
        owners = defaultdict(set)
        for link in links:
            owners[_digi_key(link)].add((int(link.eventId), int(link.trackId)))

        event_result = {
            "run": truth["run"],
            "lumi": truth["lumi"],
            "event": truth["event"],
            "signal_muons": [],
        }
        for muon in truth["signal_muons"]:
            truth_key = (muon["event_id"]["raw"], muon["track_id"])
            linked_by_subsystem = defaultdict(set)
            ambiguous_by_subsystem = defaultdict(set)
            for digi_key, digi_owners in owners.items():
                if truth_key not in digi_owners:
                    continue
                subsystem = KIND_SUBSYSTEM[digi_key[0]]
                linked_by_subsystem[subsystem].add(digi_key)
                if len(digi_owners) > 1:
                    ambiguous_by_subsystem[subsystem].add(digi_key)

            subsystem_result = {}
            for subsystem in SUBDETECTORS:
                raw_linked_keys = linked_by_subsystem[subsystem]
                linked_keys = raw_linked_keys & prepack
                links_without_prepack_digi = raw_linked_keys - prepack
                matched_keys = linked_keys & unpacked
                missing_keys = linked_keys - unpacked
                simhits = muon["subdetectors"][subsystem]["hits"]
                linked_samples = defaultdict(int)
                matched_samples = defaultdict(int)
                missing_samples = defaultdict(int)
                linked_kinds = defaultdict(int)
                missing_kinds = defaultdict(int)
                for kind, _, _, sample in linked_keys:
                    linked_samples[str(sample)] += 1
                    linked_kinds[str(kind)] += 1
                for _, _, _, sample in matched_keys:
                    matched_samples[str(sample)] += 1
                for kind, _, _, sample in missing_keys:
                    missing_samples[str(sample)] += 1
                    missing_kinds[str(kind)] += 1
                subsystem_result[subsystem] = {
                    "simhits": simhits,
                    "linked_simulated_digis": len(linked_keys),
                    "matched_unpacked_digis": len(matched_keys),
                    "linked_samples": dict(sorted(linked_samples.items(), key=lambda item: int(item[0]))),
                    "matched_samples": dict(sorted(matched_samples.items(), key=lambda item: int(item[0]))),
                    "missing_samples": dict(sorted(missing_samples.items(), key=lambda item: int(item[0]))),
                    "linked_kinds": dict(sorted(linked_kinds.items())),
                    "missing_kinds": dict(sorted(missing_kinds.items())),
                    "ambiguous_shared_digis": len(ambiguous_by_subsystem[subsystem] & prepack),
                    "links_without_prepack_digi": len(links_without_prepack_digi),
                    "linked_channels": sorted(_channel_key(key) for key in linked_keys),
                    "matched_channels": sorted(_channel_key(key) for key in matched_keys),
                    "missing_channels": sorted(_channel_key(key) for key in missing_keys),
                    "status": _subsystem_status(simhits, len(linked_keys), len(matched_keys)),
                }

            event_result["signal_muons"].append(
                {
                    "event_id": muon["event_id"],
                    "track_id": muon["track_id"],
                    "pdg_id": muon["pdg_id"],
                    "subdetectors": subsystem_result,
                    "classification": _overall_classification(subsystem_result),
                }
            )
        output["events"].append(event_result)

    classifications = defaultdict(int)
    for event in output["events"]:
        for muon in event["signal_muons"]:
            classifications[muon["classification"]] += 1
    output["summary"] = {
        "events": len(output["events"]),
        "signal_muons": sum(len(event["signal_muons"]) for event in output["events"]),
        "classifications": dict(sorted(classifications.items())),
    }

    with open(args.output, "w", encoding="utf-8") as output_file:
        json.dump(output, output_file, indent=2, sort_keys=True)
        output_file.write("\n")
    print(json.dumps(output["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
