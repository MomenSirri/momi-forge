from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

from PIL import Image

import flux2_klein_image_edit_9b_distilled as flux2
from runpod_api_class import RunpodSubmissionUncertainError


class Flux2PayloadPreparationTests(unittest.TestCase):
    def test_payload_places_values_on_expected_nodes(self) -> None:
        prepared = flux2._prepare_flux2_inputs(
            mode=flux2.MODE_EDIT,
            edit_image_count="1",
            image_1=Image.new("RGB", (32, 24), "white"),
            image_2=None,
            image_3=None,
            prompt_text="Add warm evening light",
            realistic_strength=0.5,
            workflow=flux2.WORKFLOW_NAME,
        )
        for input_path in prepared.input_paths:
            self.addCleanup(Path(input_path).unlink, missing_ok=True)

        job = flux2._build_flux2_payload(
            prepared,
            mode=flux2.MODE_EDIT,
            prompt_text="Add warm evening light",
            realistic_strength=0.5,
            workflow_debug=False,
            is_admin_user=False,
        )
        workflow = job.payload["input"]["workflow"]

        self.assertEqual(
            workflow[flux2.NODE_IMAGE_1]["inputs"]["image"],
            "flux2_klein_input_1.jpg",
        )
        self.assertEqual(
            workflow[flux2.NODE_POSITIVE_TEXT]["inputs"]["text"],
            "Add warm evening light",
        )
        self.assertEqual(
            workflow[flux2.NODE_CFG_GUIDER]["inputs"]["positive"],
            [flux2.NODE_POSITIVE_1, 0],
        )
        self.assertEqual(
            job.payload["input"]["images"][0]["name"],
            "flux2_klein_input_1.jpg",
        )


class Flux2JobStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_uncertain_submission_points_to_jobs_without_retry(self) -> None:
        class UncertainAPI:
            def __init__(self) -> None:
                self.run_calls = 0

            async def run(self, payload):
                self.run_calls += 1
                raise RunpodSubmissionUncertainError("request may still have been accepted")

        api = UncertainAPI()
        result = await flux2._submit_flux2_job(api, {"input": {}})

        self.assertTrue(result.uncertain)
        self.assertIsNone(result.job_id)
        self.assertIn("check the Jobs page", str(result.error_message))
        self.assertEqual(api.run_calls, 1)

    async def test_failed_status_returns_user_facing_event(self) -> None:
        api = _StatusAPI({"status": "FAILED", "error": "worker exploded"})

        events = [
            event
            async for event in flux2._poll_flux2_job(
                api,
                "job-1",
                SimpleNamespace(),
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
            async for event in flux2._poll_flux2_job(
                api,
                "job-1",
                SimpleNamespace(),
            )
        ]

        self.assertEqual(api.status_calls, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "terminal_failure")
        self.assertIn("CANCELLED", events[0].message)

    async def test_malformed_images_payload_is_returned_as_decode_error(self) -> None:
        result = await flux2._finalize_flux2_output(
            {"status": "COMPLETED", "output": {"images": "malformed"}},
            SimpleNamespace(),
        )

        self.assertIsNone(result.result_image)
        self.assertIn("No decodable image", str(result.error_message))


class _StatusAPI:
    def __init__(self, status: dict[str, object]) -> None:
        self._status = status
        self.status_calls = 0

    async def status(self, job_id: str) -> dict[str, object]:
        self.status_calls += 1
        return self._status


if __name__ == "__main__":
    unittest.main()
