#!/usr/bin/env python3
"""Verify paired NanoAOD truth and plot efficiency and momentum response."""
import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/jniedzie/matplotlib-lss")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import ROOT
ROOT.gROOT.SetBatch(True)
ROOT.gErrorIgnoreLevel = ROOT.kError


def read_chunk(path):
    source = ROOT.TFile.Open(str(path))
    if not source or source.IsZombie() or source.TestBit(ROOT.TFile.kRecovered):
        raise RuntimeError(f"Unreadable or recovered ROOT file: {path}")
    tree = source.Get("Events")
    if not tree:
        raise RuntimeError(f"Missing Events tree: {path}")
    branches = [b.GetName() for b in tree.GetListOfBranches()]
    truth_names = sorted(n for n in branches if n.startswith("GenPart_"))
    required = ["GenPart_pt", "GenPart_pz", "GenPart_eta", "GenPart_phi", "GenPart_mass", "GenPart_pdgId", "GenPart_status",
                "ShiftMuon_pt", "ShiftMuon_charge", "ShiftMuon_genPartIdx", "ShiftMuon_genPartDeltaR"]
    if not set(required).issubset(branches):
        raise RuntimeError(f"Missing branches in {path}: {set(required)-set(branches)}")
    tree.SetBranchStatus("*", 0)
    for name in set(truth_names + required + ["run", "luminosityBlock", "event", "nShiftMuon"]):
        tree.SetBranchStatus(name, 1)
    events = {}
    for event in tree:
        key = (int(event.run), int(event.luminosityBlock), int(event.event))
        if key in events:
            raise RuntimeError(f"Duplicate event in {path}: {key}")
        truth = {n: list(getattr(event, n)) for n in truth_names}
        digest = hashlib.sha256(json.dumps(truth, sort_keys=True, allow_nan=False).encode()).hexdigest()
        muons = {}
        for i, pdg in enumerate(truth["GenPart_pdgId"]):
            if abs(pdg) == 13 and truth["GenPart_status"][i] == 1:
                pt, eta = truth["GenPart_pt"][i], truth["GenPart_eta"][i]
                phi = truth["GenPart_phi"][i]
                muons[i] = {"pt": pt, "pz": truth["GenPart_pz"][i], "eta": eta,
                            "px": pt*math.cos(phi), "py": pt*math.sin(phi), "pdg_id": pdg,
                            "q": -1 if pdg == 13 else 1, "reco": None}
        for i, gen in enumerate(event.ShiftMuon_genPartIdx):
            if gen < 0:
                continue
            if gen not in muons:
                raise RuntimeError(f"Invalid generator association in {path}: {key}/{gen}")
            reco = {"pt": float(event.ShiftMuon_pt[i]), "q": int(event.ShiftMuon_charge[i]),
                    "dr": float(event.ShiftMuon_genPartDeltaR[i])}
            # One generated muon contributes once to efficiency, even if two
            # reconstructed candidates associate to it. Pick minimum deltaR.
            if muons[gen]["reco"] is None or reco["dr"] < muons[gen]["reco"]["dr"]:
                muons[gen]["reco"] = reco
        events[key] = {"digest": digest, "muons": muons, "reco_rows": int(event.nShiftMuon)}
    source.Close()
    return events


def wilson(k, n):
    if not n:
        return None
    p = k/n
    centre = (p+0.5/n)/(1+1/n)
    half = math.sqrt(p*(1-p)/n+0.25/n**2)/(1+1/n)
    return [centre-half, centre+half]


def response(rows, sample, common=False, quantity="q_over_pt"):
    values = []
    for row in rows:
        if common and not all(row[s]["reco"] for s in (0, 1)):
            continue
        gen = row[sample]; reco = gen["reco"]
        if reco and reco["pt"] > 0 and gen["pt"] > 0:
            value = (reco["q"]/reco["pt"])/(gen["q"]/gen["pt"])-1 if quantity == "q_over_pt" else reco["pt"]/gen["pt"]-1
            if not math.isfinite(value):
                raise RuntimeError("Non-finite matched momentum response")
            values.append(value)
    return np.asarray(values)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("control", type=Path)
    ap.add_argument("comparison", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--expected-events", type=int, required=True)
    ap.add_argument("--labels", nargs=2, default=["CMS control", "LSS material + field"])
    ap.add_argument("--transport-directory", type=Path,
                    help="comparison-sample per-chunk muon_transport JSONs; join by event and generated momentum")
    args = ap.parse_args()
    inputs = [sorted((p/"samples/step4").glob("events_NanoAOD_part_*.root")) for p in (args.control, args.comparison)]
    if not inputs[0] or [p.name for p in inputs[0]] != [p.name for p in inputs[1]]:
        raise RuntimeError("Missing or unequal chunk sets; no complete comparison possible")
    rows, fingerprints = [], []
    totals = [dict(events=0, generated_muons=0, matched_muons=0, reco_rows=0) for _ in range(2)]
    for paths in zip(*inputs):
        pair = [read_chunk(p) for p in paths]
        transport = None
        if args.transport_directory:
            part = re.search(r"part_(\d+)", paths[0].name).group(1)
            transport = json.loads((args.transport_directory/f"muon_transport_part{part}.json").read_text())["tracks"]
        if pair[0].keys() != pair[1].keys():
            raise RuntimeError(f"Event IDs differ for {paths}")
        for key in sorted(pair[0]):
            events = [p[key] for p in pair]
            if events[0]["digest"] != events[1]["digest"]:
                raise RuntimeError(f"Generator content differs in {paths[0].name}, {key}")
            if transport is not None:
                used = set()
                for g, muon in events[1]["muons"].items():
                    candidates = []
                    for tr in transport:
                        if tr["event"] != key[2] or tr["pdg_id"] != muon["pdg_id"] or tr["track_id"] in used:
                            continue
                        momentum = tr.get("start_momentum_GeV")
                        # NanoAOD pt/phi are compressed and the log is rounded
                        # to MeV. Use explicit pz, never pt*sinh(compressed eta).
                        if momentum:
                            trace_pt = math.hypot(momentum[0], momentum[1])
                            phi_difference = math.remainder(math.atan2(momentum[1],momentum[0]) - math.atan2(muon["py"],muon["px"]), 2*math.pi)
                            if (abs(momentum[2]-muon["pz"]) < .003 + 1.e-5*abs(muon["pz"])
                                    and abs(trace_pt-muon["pt"]) < .003 + .005*muon["pt"]
                                    and abs(phi_difference) < .02):
                                candidates.append(tr)
                    if len(candidates) != 1:
                        raise RuntimeError(f"Ambiguous or missing transport identity: {paths[0].name}, {key}, gen {g}")
                    tr = candidates[0]; used.add(tr["track_id"])
                    if tr.get("upstream_rock_path_m") is None:
                        raise RuntimeError("Missing per-step trace; cannot classify rock entry")
                    for event in events:
                        event["muons"][g]["upstream_rock"] = tr["upstream_rock_path_m"] > 0
            fingerprints.append([paths[0].name, key, events[0]["digest"]])
            for i, event in enumerate(events):
                totals[i]["events"] += 1
                totals[i]["reco_rows"] += event["reco_rows"]
                totals[i]["generated_muons"] += len(event["muons"])
                totals[i]["matched_muons"] += sum(m["reco"] is not None for m in event["muons"].values())
            rows.extend((events[0]["muons"][g], events[1]["muons"][g]) for g in events[0]["muons"])
    if any(t["events"] != args.expected_events for t in totals):
        raise RuntimeError(f"Event count differs from requested {args.expected_events}: {totals}")
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {"labels": args.labels, "samples": totals, "paired_events": len(fingerprints),
               "truth_fingerprint": hashlib.sha256(json.dumps(fingerprints).encode()).hexdigest(),
               "matching": "existing directional GenPart association; duplicates reduced by minimum deltaR; not a SimHit truth match",
               "response_definition": "(q/pt reco)/(q/pt gen) - 1; median and half central 68 percent width",
               "paired_muons": {"both": 0, "control_only": 0, "comparison_only": 0, "neither": 0}}
    for a,b in rows:
        group = "both" if a["reco"] and b["reco"] else "control_only" if a["reco"] else "comparison_only" if b["reco"] else "neither"
        summary["paired_muons"][group] += 1
    if args.transport_directory:
        summary["rock_groups"] = {}
        for rock in (False, True):
            group = [r for r in rows if r[1]["upstream_rock"] == rock]
            summary["rock_groups"]["entered_rock_before_cms" if rock else "no_rock_before_cms"] = {
                "generated": len(group),
                "matched": [sum(r[i]["reco"] is not None for r in group) for i in range(2)],
                "definition": "Membership fixed from comparison trajectory before the CMS entrance plane"}
    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    for axis, name, edges in zip(axes, ("pt", "pz", "eta"),
                                  ([0, .5, 1, 2, 3, 5, 10, 20, 50], [-2000,-1000,-500,-200,-100,-50,0], [-10,-8,-7,-6,-5,-4,-3,0])):
        for i,label in enumerate(args.labels):
            x, y, low, high = [], [], [], []
            for left,right in zip(edges[:-1], edges[1:]):
                selected = [r[i] for r in rows if left <= r[i][name] < right]
                n=len(selected); k=sum(r["reco"] is not None for r in selected)
                if n:
                    bounds=wilson(k,n); x.append((left+right)/2); y.append(k/n)
                    low.append(k/n-bounds[0]); high.append(bounds[1]-k/n)
            axis.errorbar(x,y,yerr=[low,high], marker="o",label=label)
        axis.set(xlabel=f"Generated {name}" + (" [GeV]" if name != "eta" else ""), ylabel="Muon reconstruction efficiency", ylim=(0,1))
    axes[0].legend(fontsize=8); fig.tight_layout(); fig.savefig(args.output/"efficiency.pdf"); fig.savefig(args.output/"efficiency.png"); plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(11,4))
    for i,label in enumerate(args.labels):
        for j,common in enumerate((False,True)):
            values=response(rows,i,common)
            key="common_reconstructed_response" if common else "matched_response"
            if len(values):
                q=np.quantile(values,[.16,.5,.84]); totals[i][key]={"n":len(values), "median":float(q[1]), "half68":float((q[2]-q[0])/2), "outside_plot_range":int(sum(abs(values)>2))}
                axes[j].hist(values,bins=np.linspace(-2,2,81),histtype="step",label=label)
            axes[j].set(xlabel="(q/pt reco)/(q/pt gen) - 1",ylabel="Muons",title="Same muons reconstructed in both" if common else "All matched muons")
        totals[i]["efficiency"]=totals[i]["matched_muons"]/totals[i]["generated_muons"]
        totals[i]["efficiency_wilson68"]=wilson(totals[i]["matched_muons"],totals[i]["generated_muons"])
    axes[0].legend(fontsize=8); fig.tight_layout(); fig.savefig(args.output/"momentum_response.pdf"); fig.savefig(args.output/"momentum_response.png"); plt.close(fig)
    fig, axes = plt.subplots(2, 2, figsize=(11,8))
    edges = [0, 25, 50, 100, 200, 500, 1000, 2000]
    for i,label in enumerate(args.labels):
        for column, common in enumerate((False, True)):
            x, medians, widths = [], [], []
            for left,right in zip(edges[:-1],edges[1:]):
                selected = [r for r in rows if left <= abs(r[0]["pz"]) < right]
                values = response(selected,i,common,quantity="pt")
                if len(values) >= 5:
                    q = np.quantile(values,[.16,.5,.84]); x.append((left+right)/2)
                    medians.append(q[1]); widths.append((q[2]-q[0])/2)
            axes[0,column].plot(x,medians,"o-",label=label)
            axes[1,column].plot(x,widths,"o-",label=label)
            axes[0,column].set(title="Same muons reconstructed in both" if common else "All matched muons", ylabel="Median (pt reco / pt gen - 1)")
            axes[1,column].set(xlabel="Generated |pz| [GeV]",ylabel="Half central 68% width")
    axes[0,0].legend(fontsize=8); fig.tight_layout(); fig.savefig(args.output/"scale_resolution.pdf"); fig.savefig(args.output/"scale_resolution.png"); plt.close(fig)
    (args.output/"summary.json").write_text(json.dumps(summary,indent=2,allow_nan=False)+"\n")
    (args.output/"generator_fingerprints.json").write_text(json.dumps(fingerprints)+"\n")
    print(json.dumps(summary,indent=2))


if __name__ == "__main__":
    main()
