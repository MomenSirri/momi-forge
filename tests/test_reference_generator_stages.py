from __future__ import annotations

from pathlib import Path
import unittest

from PIL import Image

import reference_generator as reference
from runpod_api_class import RunpodSubmissionUncertainError


class ReferencePayloadPreparationTests(unittest.TestCase):
    def test_payload_places_values_on_expected_nodes(self) -> None:
        prepared = reference._prepare_reference_inputs(
            main_image=Image.new("RGB", (40, 30), "white"),
            reference_image=Image.new("RGB", (32, 24), "gray"),
            color_strength=0.65,
            creativity=0.35,
            structure_strength=0.8,
            enhancement_enabled=True,
            color_match_enabled=False,
            workflow=reference.WORKFLOW_NAME,
        )
        job = reference._build_reference_payload(
            prepared,
            color_strength=0.65,
            creativity=0.35,
            structure_strength=0.8,
            enhancement_enabled=True,
            color_match_enabled=False,
            workflow_debug=False,
            is_admin_user=False,
        )
        workflow = job.payload["input"]["workflow"]

        self.assertEqual(
            workflow[reference.NODE_MAIN_IMAGE_INPUT]["inputs"]["image"],
            prepared.main_image_b64,
        )
        self.assertEqual(
            workflow[reference.NODE_REFERENCE_IMAGE_INPUT]["inputs"]["image"],
            prepared.reference_image_b64,
        )
        self.assertEqual(
            workflow[reference.NODE_IPADAPTER_ADVANCED]["inputs"]["weight"],
            0.65,
        )
        self.assertEqual(
            workflow[reference.NODE_PIPEKSAMPLER_BASE]["inputs"]["denoise"],
            0.35,
        )
        self.assertEqual(
            workflow[reference.NODE_APPLY_CONTROLNET]["inputs"]["strength"],
            0.8,
        )
        self.assertEqual(
            workflow[reference.NODE_ENHANCEMENT_IMAGE_ROUTER]["inputs"]["images"],
            [reference.NODE_ENHANCEMENT_DIRECT_SOURCE, 0],
        )


class ReferenceJobStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_uncertain_submission_points_to_jobs_without_retry(self) -> None:
        class UncertainAPI:
            def __init__(self) -> None:
                self.run_calls = 0

            async def run(self, payload):
                self.run_calls += 1
                raise RunpodSubmissionUncertainError("request may still have been accepted")

        api = UncertainAPI()
        result = await reference._submit_reference_job(api, {"input": {}})

        self.assertTrue(result.uncertain)
        self.assertIsNone(result.job_id)
        self.assertIn("check the Jobs page", str(result.error_message))
        self.assertEqual(api.run_calls, 1)

    async def test_failed_status_returns_user_facing_event(self) -> None:
        api = _StatusAPI({"status": "FAILED", "error": "worker exploded"})

        events = [
            event
            async for event in reference._poll_reference_job(
                api,
                "job-1",
                left_path=Path("input.png"),
                state=_poll_state(),
                stream_enabled=False,
            )
        ]

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "terminal_failure")
        self.assertEqual(events[0].title, "RunPod Error")
        self.assertIn("worker exploded", events[0].message)

    async def test_cancelled_status_stops_polling_and_reports_cancelled(self) -> None:
        api = _StatusAPI({"status": "CANCELLED"})

        events = [
            event
            async for event in reference._poll_reference_job(
                api,
                "job-1",
                left_path=Path("input.png"),
                state=_poll_state(),
                stream_enabled=False,
            )
        ]

        self.assertEqual(api.status_calls, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "terminal_failure")
        self.assertIn("CANCELLED", events[0].message)

    async def test_malformed_images_payload_is_returned_as_decode_error(self) -> None:
        result = await reference._finalize_reference_output(
            {"status": "COMPLETED", "output": {"images": "malformed"}},
            left_path=Path("input.png"),
        )

        self.assertIsNone(result.result_image)
        self.assertIn("No decodable image", str(result.error_message))


def _poll_state() -> reference.ReferencePollState:
    return reference.ReferencePollState(
        progress_tracker=reference._init_reference_progress_tracker(
            enhancement_enabled=True,
            color_match_enabled=True,
        )
    )


class _StatusAPI:
    def __init__(self, status: dict[str, object]) -> None:
        self._status = status
        self.status_calls = 0

    async def status(self, job_id: str) -> dict[str, object]:
        self.status_calls += 1
        return self._status


if __name__ == "__main__":
    unittest.main()
