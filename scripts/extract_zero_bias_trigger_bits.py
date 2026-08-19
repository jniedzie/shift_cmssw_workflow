#!/usr/bin/env python3

"""Write correlated ZeroBias L1 and HLT decisions as streaming JSON Lines."""

import argparse
import hashlib
import json
import sys

from DataFormats.FWLite import Events, Handle


SCHEMA_VERSION = 1


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Extract complete, correlated uGT bit vectors and accepted HLT paths "
            "from output of zero_bias_unpack_cfg.py"
        )
    )
    parser.add_argument("inputs", nargs="+", help="unpacked local or XRootD EDM files")
    parser.add_argument("-o", "--output", required=True, help="output JSONL path")
    parser.add_argument("--max-events", type=int, default=-1)
    parser.add_argument("--l1-process", default="SHIFTZB")
    parser.add_argument("--hlt-process", default="HLT")
    parser.add_argument(
        "--source-dataset",
        default="",
        help="dataset DID recorded as provenance; it is not queried by this script",
    )
    return parser.parse_args()


def fired_bits(decisions):
    return [index for index, decision in enumerate(decisions) if decision]


def l1_bx_record(block):
    return {
        "initial": fired_bits(block.getAlgoDecisionInitial()),
        "intermediate": fired_bits(block.getAlgoDecisionInterm()),
        "final": fired_bits(block.getAlgoDecisionFinal()),
        "final_or": bool(block.getFinalOR()),
        "final_or_pre_veto": bool(block.getFinalORPreVeto()),
        "final_or_veto": bool(block.getFinalORVeto()),
        "prescale_column": int(block.getPreScColumn()),
        # The persistent payload exposes these uint32 identifiers through an
        # int getter, so normalize negative Python values back to uint32.
        "menu_uuid": int(block.getL1MenuUUID()) & 0xFFFFFFFF,
        "firmware_uuid": int(block.getL1FirmwareUUID()) & 0xFFFFFFFF,
        "bx_in_event": int(block.getbxInEventNr()),
    }


def external_bx_record(block):
    return [index for index in range(256) if block.getExternalDecision(index)]


def main():
    args = parse_args()
    if args.max_events == 0:
        raise ValueError("--max-events must be positive or -1 for all events")

    # GlobalAlgBlk is intentionally in the global namespace; the objects it
    # describes (muons, jets, and so on) live in namespace l1t.
    l1_handle = Handle("BXVector<GlobalAlgBlk>")
    external_handle = Handle("BXVector<GlobalExtBlk>")
    hlt_handle = Handle("edm::TriggerResults")
    l1_label = ("gtStage2Digis", "", args.l1_process)
    external_label = ("gtStage2Digis", "", args.l1_process)
    hlt_label = ("TriggerResults", "", args.hlt_process)
    seen_hlt_menus = set()
    event_count = 0

    with open(args.output, "w", encoding="utf-8") as output:
        output.write(
            json.dumps(
                {
                    "record_type": "metadata",
                    "schema": "shift-zero-bias-trigger-bits",
                    "schema_version": SCHEMA_VERSION,
                    "inputs": args.inputs,
                    "source_dataset": args.source_dataset,
                    "l1_label": list(l1_label),
                    "l1_external_label": list(external_label),
                    "hlt_label": list(hlt_label),
                    "decision_semantics": {
                        "initial": "uGT algorithm decision before prescales and masks",
                        "intermediate": "uGT algorithm decision after prescales",
                        "final": "uGT algorithm decision after prescales and masks",
                    },
                },
                sort_keys=True,
            )
            + "\n"
        )

        for event in Events(args.inputs):
            if args.max_events > 0 and event_count >= args.max_events:
                break

            if not event.getByLabel(l1_label, l1_handle) or not l1_handle.isValid():
                raise RuntimeError(f"missing L1 product {l1_label} at event index {event_count}")
            if not event.getByLabel(external_label, external_handle) or not external_handle.isValid():
                raise RuntimeError(
                    f"missing L1 external product {external_label} at event index {event_count}"
                )
            if not event.getByLabel(hlt_label, hlt_handle) or not hlt_handle.isValid():
                raise RuntimeError(f"missing HLT product {hlt_label} at event index {event_count}")

            trigger_results = hlt_handle.product()
            trigger_names = event.object().triggerNames(trigger_results)
            paths = [str(trigger_names.triggerName(i)) for i in range(trigger_results.size())]
            menu_id = hashlib.sha256("\0".join(paths).encode("utf-8")).hexdigest()[:16]
            if menu_id not in seen_hlt_menus:
                output.write(
                    json.dumps(
                        {
                            "record_type": "hlt_menu",
                            "menu_id": menu_id,
                            "paths": paths,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                seen_hlt_menus.add(menu_id)

            accepted_hlt = []
            error_hlt = []
            for index, path in enumerate(paths):
                if trigger_results.accept(index):
                    accepted_hlt.append(path)
                if trigger_results.error(index):
                    error_hlt.append(path)

            l1_product = l1_handle.product()
            l1_by_bx = {}
            for bx in range(l1_product.getFirstBX(), l1_product.getLastBX() + 1):
                blocks = [l1_bx_record(l1_product.at(bx, index)) for index in range(l1_product.size(bx))]
                l1_by_bx[str(bx)] = blocks

            external_product = external_handle.product()
            l1_external_by_bx = {}
            for bx in range(external_product.getFirstBX(), external_product.getLastBX() + 1):
                blocks = [
                    external_bx_record(external_product.at(bx, index))
                    for index in range(external_product.size(bx))
                ]
                l1_external_by_bx[str(bx)] = blocks

            auxiliary = event.object().eventAuxiliary()
            output.write(
                json.dumps(
                    {
                        "record_type": "event",
                        "source_event_index": event_count,
                        "run": int(auxiliary.run()),
                        "lumi": int(auxiliary.luminosityBlock()),
                        "event": int(auxiliary.event()),
                        "orbit": int(auxiliary.orbitNumber()),
                        "bx": int(auxiliary.bunchCrossing()),
                        "is_real_data": bool(auxiliary.isRealData()),
                        "l1_by_bx": l1_by_bx,
                        "l1_external_by_bx": l1_external_by_bx,
                        "hlt_menu_id": menu_id,
                        "hlt_accepted": accepted_hlt,
                        "hlt_errors": error_hlt,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            event_count += 1

    print(f"wrote {event_count} events and {len(seen_hlt_menus)} HLT menu(s) to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
