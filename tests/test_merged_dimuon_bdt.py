"""Worker safety checks without a scheduler, EOS mutation or reconstruction."""

import importlib.util
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import run_merged_dimuon_bdt as worker


def expected_legacy_cutflow(weights, accumulator=0.0):
    """Reference for GetAs<float> and ordered map<string,float> += weight.

    C++ converts a Double branch to float before addition, then rounds the
    accumulator to float after every event. Keep order across input chunks.
    TH1D storage only converts the completed float sum back to Double.
    """
    def float32(value):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Nonfinite event weight")
        try:
            converted = struct.unpack("f", struct.pack("f", value))[0]
        except OverflowError as error:
            raise ValueError("Legacy float cutflow would overflow") from error
        if not math.isfinite(converted):
            raise ValueError("Legacy float cutflow would overflow")
        return converted
    accumulator = float32(accumulator)
    for weight in weights:
        accumulator = float32(accumulator + float32(weight))
    return accumulator


class MergedDimuonBdtWorkerTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "source.root"
        self.source.write_bytes(b"immutable merged MC fixture")
        self.plan_hash = "f" * 64
        self.row = {
            "index": 0, "source_transport_path": str(self.source),
            "source_logical_path": "/source/qcd/merged.root",
            "source_sha256": worker.sha(self.source), "source_bytes": self.source.stat().st_size,
            "source_adler32": worker.adler(self.source), "expected_events": 20,
            "weight_branch": "shiftSamplingGenWeight",
            "complete_marker": "/new/metadata/complete.json",
            "output_nano": "/new/nanoAOD/scored.root",
            "output_histograms": "/new/histograms/combined.root",
            "score_receipt": "/new/metadata/score.json",
            "bdt_hist_receipt": "/new/metadata/bdt_hist.json",
            "score_log": "/new/metadata/score.log",
            "histogram_log": "/new/metadata/histogram.log",
        }
        self.plan = {
            "classifier_directory": str(self.root / "classifier"), "model": str(self.root / "model.json"),
            "model_sha256": "b" * 64,
            "bdt_histogrammer": str(self.root / "tea/utils/shift_bdt_histogrammer.py"),
        }
        self.remote = {}
        self.operations = []
        self.commands = []
        self.score_events = 20
        self.hist_events = 20
        self.hist_pairs = 3
        self.fail_histogrammer = False
        self.tamper_readback = None
        self.standard_check = mock.Mock()
        self.legacy_sum = mock.Mock(return_value=17.249996185302734)
        self.work = self.root / "work"

        for name, replacement in (
            ("remote_exists", lambda path: path in self.remote),
            ("get_remote", self.get_remote), ("put_remote", self.put_remote),
            ("transfer", lambda argv: self.operations.append(("mkdir", tuple(argv)))),
        ):
            patch = mock.patch.object(worker.io, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(worker.subprocess, "run", self.run_subprocess)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(worker, "check_standard_histograms", self.standard_check)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(worker, "legacy_weight_sum", self.legacy_sum)
        patch.start()
        self.addCleanup(patch.stop)

    def get_remote(self, path, local):
        self.operations.append(("get", path))
        payload = self.remote[path]
        if path == self.tamper_readback:
            payload += b"readback corruption"
        Path(local).write_bytes(payload)

    def put_remote(self, local, path):
        if path in self.remote:
            raise FileExistsError(path)
        self.operations.append(("put", path))
        self.remote[path] = Path(local).read_bytes()

    def run_subprocess(self, argv, **kwargs):
        self.commands.append(argv)
        if argv[0] == "hadd":
            Path(argv[1]).write_bytes(Path(argv[2]).read_bytes() + Path(argv[3]).read_bytes())
        elif Path(argv[1]).name == "add_bdt_score.py":
            output = Path(argv[argv.index("--output") + 1])
            output.write_bytes(b"saved Double score fixture; " + self.source.read_bytes())
            receipt = {
                "complete": True, "events": self.score_events, "retained_pairs": 3,
                "source": str(self.work / "source.root"), "output": str(output),
                "source_sha256": worker.sha(self.source), "output_sha256": worker.sha(output),
                "model_sha256": "b" * 64, "selection_applied": False,
                "verification": {"all_original_content_equal": True, "all_scores_recomputed_equal": True},
            }
            Path(argv[argv.index("--receipt") + 1]).write_text(json.dumps(receipt))
        elif Path(argv[1]).name == "shift_bdt_histogrammer.py":
            if self.fail_histogrammer:
                raise subprocess.CalledProcessError(1, argv, "Missing saved score even in zero-pair source")
            output = Path(argv[argv.index("--output") + 1])
            output.write_bytes(b"supplemental BDT histogram fixture")
            receipt = {
                "complete": True, "events": self.hist_events, "pairs": self.hist_pairs,
                "input": str(self.work / "scored.root"), "output": str(output),
                "output_sha256": worker.sha(output), "event_sumw": 17.25,
                "score_selection_applied": False, "weight_application_count": 1,
            }
            Path(argv[argv.index("--receipt") + 1]).write_text(json.dumps(receipt))
        else:
            self.assertEqual(Path(argv[0]).name, "shift_histogrammer")
            Path(argv[argv.index("--output_hists_path") + 1]).write_bytes(b"ordinary histogram fixture")
        return subprocess.CompletedProcess(argv, 0)

    def run_file(self):
        return worker.run_file(self.plan, self.row, self.work, self.root / "cache",
                               self.root / "analysis", self.plan_hash)

    def marker(self):
        artifacts = {
            "nano": self.row["output_nano"], "histograms": self.row["output_histograms"],
            **{key: self.row[key] for key in ("score_receipt", "bdt_hist_receipt", "score_log", "histogram_log")},
        }
        nano_hash_path = self.root / "marker_nano"
        nano_hash_path.write_bytes(b"nano")
        hist_hash_path = self.root / "marker_histograms"
        hist_hash_path.write_bytes(b"histograms")
        receipts = {
            "score_receipt": {
                "complete": True, "source_sha256": worker.sha(self.source),
                "output_sha256": worker.sha(nano_hash_path), "model_sha256": "b" * 64,
                "events": 20, "retained_pairs": 3, "selection_applied": False,
                "verification": {"all_original_content_equal": True, "all_scores_recomputed_equal": True},
            },
            "bdt_hist_receipt": {
                "complete": True, "events": 20, "pairs": 3,
                "output_sha256": worker.sha(hist_hash_path),
            },
        }
        published = {}
        for name, path in artifacts.items():
            self.remote[path] = json.dumps(receipts[name]).encode() if name in receipts else name.encode()
            local = self.root / name
            local.write_bytes(self.remote[path])
            published[name] = {"path": path, "sha256": worker.sha(local), "bytes": local.stat().st_size}
        return {
            "schema": "shift-merged-dimuon-bdt-complete-v1", "complete": True,
            "plan_sha256": self.plan_hash, "source_sha256": worker.sha(self.source),
            "source": self.row["source_logical_path"], "events": 20, "pairs": 3,
            "model_sha256": "b" * 64, "selection_applied": False, "published": published,
        }

    def install_marker(self, marker):
        self.remote[self.row["complete_marker"]] = json.dumps(marker).encode()

    def test_original_source_is_unchanged_and_marker_is_published_last(self):
        original = self.source.read_bytes()
        result = self.run_file()
        self.assertTrue(result["complete"])
        self.assertFalse(result["reused"])
        self.assertEqual(self.source.read_bytes(), original)
        puts = [path for kind, path in self.operations if kind == "put"]
        self.assertEqual(len(puts), 7)
        self.assertEqual(puts[-1], self.row["complete_marker"])
        self.assertEqual(len(result["marker"]["published"]), 6)
        self.standard_check.assert_called_once_with(self.work / "standard.root", 20, self.legacy_sum.return_value)
        self.legacy_sum.assert_called_once_with(self.work / "scored.root", "shiftSamplingGenWeight")
        histogram_receipt = json.loads(self.remote[self.row["bdt_hist_receipt"]])
        self.assertEqual(histogram_receipt["event_sumw"], 17.25)
        self.assertEqual(histogram_receipt["standard_cutflow_float32_sumw"], self.legacy_sum.return_value)
        bdt_command = next(command for command in self.commands if "shift_bdt_histogrammer.py" in str(command))
        self.assertEqual(bdt_command[bdt_command.index("--classifier-directory") + 1], self.plan["classifier_directory"])
        self.assertEqual(bdt_command[bdt_command.index("--weight-branch") + 1], "shiftSamplingGenWeight")
        combined = self.remote[self.row["output_histograms"]]
        self.assertEqual(combined, b"ordinary histogram fixturesupplemental BDT histogram fixture")

    def test_supplement_receipt_identifies_final_combined_histogram_checksum(self):
        self.run_file()
        receipt = json.loads(self.remote[self.row["bdt_hist_receipt"]])
        combined = self.root / "published_combined.root"
        combined.write_bytes(self.remote[self.row["output_histograms"]])
        self.assertEqual(receipt["output"], self.row["output_histograms"])
        self.assertEqual(receipt["output_sha256"], worker.sha(combined))
        self.assertNotEqual(receipt["supplemental_histogram_sha256"], receipt["output_sha256"])

    def test_source_checksum_tampering_stops_before_scoring_or_publication(self):
        self.source.write_bytes(b"another equal-size fixture!!")
        with self.assertRaisesRegex(ValueError, "frozen checksum"):
            self.run_file()
        self.assertFalse(self.commands)
        self.assertFalse(self.remote)

    def test_source_byte_count_tampering_is_rejected(self):
        self.row["source_sha256"] = None
        self.row["source_bytes"] += 1
        with self.assertRaisesRegex(ValueError, "byte count"):
            self.run_file()

    def test_source_adler_tampering_is_rejected_without_sha_authority(self):
        self.row["source_sha256"] = None
        self.row["source_adler32"] = "00000000"
        with self.assertRaisesRegex(ValueError, "Adler"):
            self.run_file()

    def test_expected_event_count_mismatch_does_not_publish(self):
        self.score_events = 19
        with self.assertRaisesRegex(ValueError, "preserve"):
            self.run_file()
        self.assertFalse(self.remote)

    def test_supplement_population_mismatch_does_not_publish(self):
        self.hist_pairs = 2
        with self.assertRaisesRegex(ValueError, "event/pair"):
            self.run_file()
        self.assertFalse(self.remote)

    def test_failed_zero_pair_score_preflight_does_not_publish(self):
        self.fail_histogrammer = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_file()
        self.assertFalse(self.remote)

    def test_orphan_histogram_is_preserved_without_overwrite(self):
        self.remote[self.row["output_histograms"]] = b"existing partial"
        with self.assertRaises(FileExistsError):
            self.run_file()
        self.assertEqual(self.remote[self.row["output_histograms"]], b"existing partial")
        self.assertFalse(self.commands)

    def test_readback_failure_never_publishes_completion_marker(self):
        self.tamper_readback = self.row["output_histograms"]
        with self.assertRaisesRegex(ValueError, "readback differs"):
            self.run_file()
        self.assertNotIn(self.row["complete_marker"], self.remote)

    def test_exact_complete_publication_reuses_without_scoring(self):
        self.install_marker(self.marker())
        result = self.run_file()
        self.assertTrue(result["reused"])
        self.assertFalse(self.commands)

    def test_complete_marker_missing_artifacts_is_rejected(self):
        marker = self.marker()
        marker["published"] = {}
        self.install_marker(marker)
        with self.assertRaises(ValueError):
            self.run_file()

    def test_complete_marker_redirecting_planned_artifact_is_rejected(self):
        marker = self.marker()
        marker["published"]["nano"]["path"] = "/unrelated/scored.root"
        self.remote["/unrelated/scored.root"] = self.remote[self.row["output_nano"]]
        self.install_marker(marker)
        with self.assertRaises(ValueError):
            self.run_file()

    def test_complete_marker_with_wrong_plan_is_rejected(self):
        marker = self.marker()
        marker["plan_sha256"] = "0" * 64
        self.install_marker(marker)
        with self.assertRaisesRegex(ValueError, "exact plan"):
            self.run_file()


class MergedBdtPlanTest(unittest.TestCase):
    def test_changed_frozen_dependency_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dependency = root / "labels.py"
            dependency.write_text("original labels")
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps({
                "schema": "shift-merged-dimuon-bdt-plan-v1", "selection_applied": False,
                "dependencies": {"labels": {"path": str(dependency), "sha256": worker.sha(dependency)}},
            }))
            plan_hash = worker.sha(plan_path)
            dependency.write_text("different labels")
            with self.assertRaisesRegex(ValueError, "dependency changed"):
                worker.validate_plan(plan_path, plan_hash)

    def test_changed_plan_is_rejected_before_dependency_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan = Path(temporary) / "plan.json"
            plan.write_text("{}"); expected = worker.sha(plan)
            plan.write_text('{"changed": true}')
            with self.assertRaisesRegex(ValueError, "plan changed"):
                worker.validate_plan(plan, expected)


class LegacyFloatCutflowReferenceTest(unittest.TestCase):
    def test_double_branch_is_cast_to_float_before_adding(self):
        weights = [16777217.0, -16777216.0]
        self.assertEqual(sum(weights), 1.0)
        self.assertEqual(expected_legacy_cutflow(weights), 0.0)

    def test_accumulation_preserves_event_order(self):
        self.assertEqual(expected_legacy_cutflow([1e8, 1, -1e8]), 0)
        self.assertEqual(expected_legacy_cutflow([1e8, -1e8, 1]), 1)

    def test_th1d_storage_does_not_restore_lost_float_precision(self):
        observed = expected_legacy_cutflow([0.1] * 1000)
        self.assertEqual(observed, 99.9990463256836)
        self.assertFalse(math.isclose(observed, 100.0, rel_tol=1e-9, abs_tol=1e-12))

    def test_chunk_boundaries_preserve_exact_ordered_result(self):
        weights = [0.1] * 30 + [1e7, -1e7, 0, -0.03] + [0.5] * 37
        expected = expected_legacy_cutflow(weights)
        result = expected_legacy_cutflow(weights[:13])
        result = expected_legacy_cutflow(weights[13:32], result)
        result = expected_legacy_cutflow(weights[32:], result)
        self.assertEqual(result, expected)

    def test_nonfinite_or_overflowing_weights_are_rejected(self):
        for value in (float("nan"), float("inf"), 1e300):
            with self.assertRaises(ValueError):
                expected_legacy_cutflow([value])

    @unittest.skipUnless(shutil.which("c++"), "Requires native C++ compiler")
    def test_reference_matches_native_cpp_float_cast_and_accumulation(self):
        weights = [0.1] * 1000 + [16777217.0, -16777216.0, 0.0, -0.03]
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "float_cutflow"
            # Standalone numerical fixture; no shared release/library rebuild.
            source = """#include <iostream>
#include <iomanip>
int main() { float total=0; double weight; while(std::cin >> weight) total += static_cast<float>(weight);
std::cout << std::setprecision(17) << static_cast<double>(total); }
"""
            subprocess.run([shutil.which("c++"), "-O2", "-std=c++17", "-x", "c++", "-o", str(executable), "-"],
                           input=source, text=True, check=True, capture_output=True, timeout=30)
            actual = subprocess.run([str(executable)], input="\n".join(map(repr, weights)),
                                    text=True, check=True, capture_output=True, timeout=10)
            self.assertEqual(float(actual.stdout), expected_legacy_cutflow(weights))


@unittest.skipUnless(importlib.util.find_spec("numpy") and importlib.util.find_spec("uproot"),
                     "Real file validation requires NumPy and uproot")
class LegacyFloatCutflowFileTest(unittest.TestCase):
    def fixture(self, directory, weights, branch="shiftSamplingGenWeight"):
        import numpy as np
        import uproot
        path = Path(directory) / "weights.root"
        with uproot.recreate(path) as output:
            tree = output.mktree("Events", {branch: "float64", "alternativeWeight": "float64"})
            tree.extend({branch: np.asarray(weights, dtype=np.float64),
                         "alternativeWeight": np.ones(len(weights), dtype=np.float64)})
        return path

    def test_real_double_branch_matches_reference_across_uproot_chunk_boundaries(self):
        weights = [0.1] * 997 + [16777217, -16777216, 1e8, -1e8, -0.03] + [0.1] * 1005
        with tempfile.TemporaryDirectory() as temporary:
            path = self.fixture(temporary, weights)
            self.assertEqual(worker.legacy_weight_sum(path, "shiftSamplingGenWeight"),
                             expected_legacy_cutflow(weights))

    def test_real_double_input_is_rounded_before_the_addition(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self.fixture(temporary, [16777217, -16777216])
            self.assertEqual(worker.legacy_weight_sum(path, "shiftSamplingGenWeight"), 0)

    def test_real_signal_weight_branch_is_used_explicitly(self):
        weights = [1, -1, 0, 0.5]
        with tempfile.TemporaryDirectory() as temporary:
            path = self.fixture(temporary, weights, "genWeight")
            self.assertEqual(worker.legacy_weight_sum(path, "genWeight"), expected_legacy_cutflow(weights))
            self.assertEqual(worker.legacy_weight_sum(path, "alternativeWeight"), len(weights))

    def test_real_nonfinite_weights_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self.fixture(temporary, [1, float("nan")])
            with self.assertRaisesRegex(ValueError, "nonfinite"):
                worker.legacy_weight_sum(path, "shiftSamplingGenWeight")


if __name__ == "__main__":
    unittest.main()
