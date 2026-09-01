#!/usr/bin/env python3

import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from classify_shift_event_capture import (  # noqa: E402
    _load_reports,
    _validate_response_identity,
)


def capture_report(offset, fingerprint="abc"):
    return {
        "schema_version": 2,
        "simhit_reference_timing": {
            "bx_offset": offset,
            "phase_ns": 0.0,
            "applied_shift_ns": 25.0 * offset,
            "model_version": "same-simhit-reference-v1",
        },
        "events": [
            {
                "run": 1,
                "lumi": 1,
                "event": 1,
                "signal_muons": [
                    {
                        "event_id": {"raw": 0},
                        "track_id": 4,
                        "subdetectors": {
                            "CSC": {
                                "simhits": 6,
                                "simhit_non_timing_sha256": fingerprint,
                                "linked_channels": [],
                                "matched_channels": [],
                            }
                        },
                    }
                ],
            }
        ],
    }


class ShiftResponseProvenanceTest(unittest.TestCase):
    def write_report(self, report):
        temporary = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        self.addCleanup(Path(temporary.name).unlink, missing_ok=True)
        json.dump(report, temporary)
        temporary.close()
        return temporary.name

    def test_embedded_offset_must_match_operator_label(self):
        path = self.write_report(capture_report(1))
        with self.assertRaisesRegex(RuntimeError, "EDM provenance says"):
            _load_reports([f"0={path}"], 2, "capture")

    def test_phase_must_match_explicit_classifier_selection(self):
        report = capture_report(0)
        report["simhit_reference_timing"]["phase_ns"] = 2.5
        path = self.write_report(report)
        with self.assertRaisesRegex(RuntimeError, "expected 0.0"):
            _load_reports([f"0={path}"], 2, "capture")
        reports, _ = _load_reports([f"0={path}"], 2, "capture", 2.5)
        self.assertEqual(reports[0]["simhit_reference_timing"]["phase_ns"], 2.5)

    def test_non_timing_simhit_mismatch_fails_closed(self):
        path0 = self.write_report(capture_report(0, "aaa"))
        path1 = self.write_report(capture_report(1, "bbb"))
        reports, _ = _load_reports(
            [f"0={path0}", f"1={path1}"], 2, "capture"
        )
        with self.assertRaisesRegex(RuntimeError, "different non-timing SimHits"):
            _validate_response_identity(reports, "capture")


if __name__ == "__main__":
    unittest.main()
