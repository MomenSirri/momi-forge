from __future__ import annotations

import unittest
from typing import Any

import General_Enhancement_v04 as general
import reference_generator as reference
import utils
from workflow_progress import ProgressTracker


class ReferenceProgressCharacterizationTests(unittest.TestCase):
    def test_weighted_stage_sequence_and_unknown_node(self) -> None:
        tracker = reference._init_reference_progress_tracker(
            enhancement_enabled=True,
            color_match_enabled=True,
        )
        sequence = [
            "Running node 42: LoadImage",
            "Running node 999: UnknownNode",
            "[comfy-log][progress] node=12 10/20",
            "[comfy-log][progress] node=16 3/10",
            "[comfy-log][progress] node=136 4/20",
            "Running node 149: ColorMatch",
            "execution finished",
        ]

        snapshots: list[tuple[int, str, str]] = []
        for progress_text in sequence:
            tracker.observe_text(progress_text)
            snapshots.append(
                (
                    tracker.overall_percent(),
                    str(tracker["phase"]),
                    str(tracker["current_status"]),
                )
            )

        self.assertEqual(
            snapshots,
            [
                (1, "Preparation", "Loading main image..."),
                (1, "Preparation", "Loading main image..."),
                (36, "Base Sampling", "Base Sampling - step 10/20"),
                (52, "Upscale Pass", "Upscale Pass - step 3/10"),
                (67, "Enhancement", "Enhancement - step 4/20"),
                (88, "Color Match", "Matching colors..."),
                (98, "Saving Output", "Execution finished. Collecting output..."),
            ],
        )


class GeneralProgressCharacterizationTests(unittest.TestCase):
    def test_workload_curve_reconciles_repeated_sampler_cycle(self) -> None:
        tracker = general._init_progress_tracker(
            image_width=1800,
            image_height=900,
            general_enhance=True,
            advance_details=True,
            body_enhance=True,
        )
        sequence = [
            "Starting job and validating input",
            "[comfy-log][node] 32 KSampler",
            "[comfy-log][progress] node=32 90/100",
            "[comfy-log][progress] node=32 1/100",
            "[comfy-log][progress] node=32 100/100",
            "[comfy-log][enhance-item] node=22 done=1 total=2",
            "[comfy-log][enhance-step] node=52 item=1/2 step=9/10",
            "[comfy-log][progress] node=52 1/10",
            "[comfy-log][node] 83 SaveImage",
            "job completed. returning",
        ]

        snapshots: list[tuple[int, str, int, int, int]] = []
        for progress_text in sequence:
            tracker.observe_text(progress_text)
            snapshots.append(
                (
                    tracker.overall_percent(),
                    str(tracker["phase"]),
                    int(tracker["stages"][general.STAGE_GENERAL]["done"]),
                    int(tracker["stages"][general.STAGE_BODY]["done"]),
                    int(round(float(tracker["wrap_ratio"]) * 100)),
                )
            )

        self.assertEqual(
            snapshots,
            [
                (0, "Preparation", 0, 0, 0),
                (0, "General Enhancement", 0, 0, 0),
                (14, "General Enhancement", 0, 0, 0),
                (15, "General Enhancement", 1, 0, 0),
                (31, "General Enhancement", 2, 0, 0),
                (46, "Advance Details", 2, 0, 0),
                (64, "Body Enhancement", 2, 0, 0),
                (67, "Body Enhancement", 2, 1, 0),
                (92, "Wrap-up", 2, 1, 93),
                (92, "Wrap-up", 2, 1, 99),
            ],
        )


class PhaseProgressCharacterizationTests(unittest.TestCase):
    @staticmethod
    def _phase_tracker() -> ProgressTracker:
        profile = utils._resolve_workflow_profile("Pro Upscaler")
        return ProgressTracker.for_phases(workflow_profile=profile)

    def test_phase_curve_reconciles_near_completion_and_step_reset(self) -> None:
        tracker = self._phase_tracker()
        sequence = [
            "Starting job and validating input",
            "[comfy-log][node] 999 UnknownNode",
            "[comfy-log][seedvr-frames] total=6",
            "[comfy-log][seedvr-upscale] 3/6",
            "[comfy-log][seedvr-upscale] 5/6",
            "[comfy-log][node] 80:12 EnhancementSampler",
            "[comfy-log][progress] node=80:12 9/10",
            "[comfy-log][progress] node=80:12 1/10",
            "[comfy-log][enhance-item] node=80:12 done=5 total=6",
            "[comfy-log][node] 97 SaveImage",
            "fetching execution history",
        ]

        snapshots: list[tuple[int, str, int, int, int]] = []
        for progress_text in sequence:
            tracker.observe_text(progress_text)
            snapshots.append(
                (
                    tracker.overall_percent(),
                    str(tracker["phase"]),
                    int(tracker["upscale_done"]),
                    int(tracker["enhance_done"]),
                    int(round(float(tracker["wrap_ratio"]) * 100)),
                )
            )

        self.assertEqual(
            snapshots,
            [
                (1, "Preparation", 0, 0, 0),
                (1, "Preparation", 0, 0, 0),
                (12, "Upscaling", 0, 0, 0),
                (35, "Upscaling", 3, 0, 0),
                (50, "Upscaling", 5, 0, 0),
                (58, "Enhancement", 6, 0, 0),
                (58, "Enhancement", 6, 0, 0),
                (63, "Enhancement", 6, 1, 0),
                (85, "Enhancement", 6, 5, 0),
                (92, "Wrap-up", 6, 6, 20),
                (94, "Wrap-up", 6, 6, 45),
            ],
        )


if __name__ == "__main__":
    unittest.main()
