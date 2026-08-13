from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import utils


class TraceRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.trace_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(utils, "RUNPOD_TRACE_DIR", self.trace_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_trace(self, name: str, *, age_seconds: float) -> Path:
        path = self.trace_dir / name
        path.write_text("{}\n", encoding="utf-8")
        stamp = 1_000_000.0 - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def test_only_the_newest_traces_survive(self) -> None:
        newest = self._write_trace("runpod_trace_a_job1_x.jsonl", age_seconds=0)
        middle = self._write_trace("runpod_trace_a_job2_x.jsonl", age_seconds=60)
        oldest = self._write_trace("runpod_trace_a_job3_x.jsonl", age_seconds=120)

        removed = utils._prune_trace_files(keep=2)

        self.assertEqual(removed, [oldest])
        self.assertTrue(newest.exists())
        self.assertTrue(middle.exists())
        self.assertFalse(oldest.exists())

    def test_unrelated_files_and_folders_are_never_touched(self) -> None:
        self._write_trace("runpod_trace_a_job1_x.jsonl", age_seconds=0)
        self._write_trace("runpod_trace_a_job2_x.jsonl", age_seconds=60)
        notes = self.trace_dir / "runpod_trace_notes.md"
        notes.write_text("keep me", encoding="utf-8")
        unrelated = self.trace_dir / "workflow_debug.json"
        unrelated.write_text("keep me too", encoding="utf-8")
        nested = self.trace_dir / "workflow_debug"
        nested.mkdir()
        (nested / "runpod_trace_nested_job_x.jsonl").write_text("{}", encoding="utf-8")

        utils._prune_trace_files(keep=1)

        self.assertTrue(notes.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue((nested / "runpod_trace_nested_job_x.jsonl").exists())

    def test_retention_of_zero_keeps_everything(self) -> None:
        kept = [
            self._write_trace(f"runpod_trace_a_job{index}_x.jsonl", age_seconds=index)
            for index in range(5)
        ]

        self.assertEqual(utils._prune_trace_files(keep=0), [])
        for path in kept:
            self.assertTrue(path.exists())

    def test_missing_trace_dir_is_not_an_error(self) -> None:
        with mock.patch.object(utils, "RUNPOD_TRACE_DIR", self.trace_dir / "gone"):
            self.assertEqual(utils._prune_trace_files(keep=1), [])

    def test_new_trace_file_is_swept_but_not_deleted(self) -> None:
        stale = self._write_trace("runpod_trace_a_job1_x.jsonl", age_seconds=600)

        with (
            mock.patch.object(utils, "RUNPOD_TRACE_DEBUG", True),
            mock.patch.object(utils, "RUNPOD_TRACE_RETENTION_FILES", 1),
        ):
            first = utils._init_trace_file(job_id="job-2", workflow="General Enhancement")
            self.assertIsNotNone(first)
            assert first is not None
            first.write_text("{}\n", encoding="utf-8")
            second = utils._init_trace_file(job_id="job-3", workflow="General Enhancement")

        # The second call swept the stale file and kept the newest one, and the
        # file it just named is created after the sweep, so it is never a
        # deletion candidate.
        self.assertFalse(stale.exists())
        self.assertTrue(first.exists())
        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second.parent, self.trace_dir)

    def test_tracing_disabled_leaves_files_alone(self) -> None:
        stale = self._write_trace("runpod_trace_a_job1_x.jsonl", age_seconds=600)

        with (
            mock.patch.object(utils, "RUNPOD_TRACE_DEBUG", False),
            mock.patch.object(utils, "RUNPOD_TRACE_RETENTION_FILES", 1),
        ):
            self.assertIsNone(
                utils._init_trace_file(job_id="job-2", workflow="General Enhancement")
            )

        self.assertTrue(stale.exists())


class IntEnvTests(unittest.TestCase):
    def test_invalid_values_fall_back_to_the_default(self) -> None:
        with mock.patch.dict(os.environ, {"MOMI_TEST_INT": "not-a-number"}):
            self.assertEqual(utils._int_env("MOMI_TEST_INT", 7), 7)

    def test_blank_values_fall_back_to_the_default(self) -> None:
        with mock.patch.dict(os.environ, {"MOMI_TEST_INT": "   "}):
            self.assertEqual(utils._int_env("MOMI_TEST_INT", 7), 7)

    def test_valid_value_is_used(self) -> None:
        with mock.patch.dict(os.environ, {"MOMI_TEST_INT": "42"}):
            self.assertEqual(utils._int_env("MOMI_TEST_INT", 7), 42)


if __name__ == "__main__":
    unittest.main()
