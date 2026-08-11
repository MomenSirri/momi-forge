from __future__ import annotations

import unittest

from PIL import Image

import server_upscaler_with_flux_enhancement as upscaler
from runpod_api_class import RunpodSubmissionUncertainError


class UpscalerPayloadPreparationTests(unittest.TestCase):
    def test_payload_places_values_on_expected_nodes(self) -> None:
        prepared = upscaler._prepare_upscaler_inputs(
            image=Image.new("RGB", (40, 30), "white"),
            engine_choice="Normal",
            enhancement=True,
            upscale_value="x2",
            flux_creativity_tilet=25,
            workflow=upscaler.WORKFLOW_NAME,
        )
        job = upscaler._build_upscaler_payload(
            prepared,
            workflow_debug=False,
            is_admin_user=False,
        )
        workflow = job.payload["input"]["workflow"]

        self.assertEqual(
            workflow["99"]["inputs"]["image"],
            "main_image_name",
        )
        self.assertEqual(workflow["80:84"]["inputs"]["value"], 25.0)
        self.assertEqual(workflow["96:82"]["inputs"]["image"], ["99", 0])
        self.assertEqual(workflow["97"]["inputs"]["images"], ["81:13", 0])
        self.assertEqual(workflow["96:85"]["inputs"]["scale_by"], 2)
        self.assertEqual(
            workflow["81:38"]["inputs"]["image"],
            ["80:14", 0],
        )
        self.assertEqual(
            workflow["80:83"]["inputs"]["image"],
            ["77:78", 0],
        )
        self.assertEqual(
            job.payload["input"]["images"][0]["name"],
            "main_image_name",
        )


class UpscalerJobStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_uncertain_submission_points_to_jobs_without_retry(self) -> None:
        class UncertainAPI:
            def __init__(self) -> None:
                self.run_calls = 0

            async def run(self, payload):
                self.run_calls += 1
                raise RunpodSubmissionUncertainError(
                    "request may still have been accepted"
                )

        api = UncertainAPI()
        result = await upscaler._submit_upscaler_job(api, {"input": {}})

        self.assertTrue(result.uncertain)
        self.assertIsNone(result.job_id)
        self.assertIn("check the Jobs page", str(result.error_message))
        self.assertEqual(api.run_calls, 1)

    async def test_failed_status_returns_user_facing_event(self) -> None:
        api = _StatusAPI({"status": "FAILED", "error": "worker exploded"})

        events = [
            event
            async for event in upscaler._poll_upscaler_job(
                api,
                "job-1",
                input_pil=Image.new("RGB", (40, 30), "white"),
                state=_poll_state(),
                stream_enabled=False,
            )
        ]

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "terminal_failure")
        self.assertIn("worker exploded", events[0].message)

    async def test_cancelled_status_stops_polling_and_reports_cancelled(self) -> None:
        api = _StatusAPI({"status": "CANCELLED"})

        events = [
            event
            async for event in upscaler._poll_upscaler_job(
                api,
                "job-1",
                input_pil=Image.new("RGB", (40, 30), "white"),
                state=_poll_state(),
                stream_enabled=False,
            )
        ]

        self.assertEqual(api.status_calls, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "cancelled")
        self.assertIn("cancelled", events[0].message.lower())

    async def test_malformed_images_payload_is_returned_as_decode_error(self) -> None:
        result = await upscaler._finalize_upscaler_output(
            {"status": "COMPLETED", "output": {"images": "malformed"}},
            input_pil=Image.new("RGB", (40, 30), "white"),
            job_id="job-1",
        )

        self.assertIsNone(result.result_image)
        self.assertIn("No decodable image", str(result.error_message))


def _poll_state() -> upscaler.UpscalerPollState:
    return upscaler.UpscalerPollState(
        phase_tracker=upscaler.ProgressTracker.for_phases(
            workflow_profile=upscaler._resolve_workflow_profile(
                upscaler.WORKFLOW_NAME
            ),
            tile_estimate={},
        ),
        trace_file=None,
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
