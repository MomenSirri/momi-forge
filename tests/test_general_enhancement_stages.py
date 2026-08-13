from __future__ import annotations

import unittest

import numpy as np

import General_Enhancement_v04 as general
from runpod_api_class import RunpodSubmissionUncertainError


class GeneralPayloadPreparationTests(unittest.TestCase):
    def test_payload_places_values_on_expected_nodes(self) -> None:
        source = general._prepare_general_source(
            image_editor_value={
                "background": np.zeros((32, 40, 3), dtype=np.uint8),
                "layers": [],
            },
            general_enhance=True,
            advance_details=True,
            additional_detail_pass=0.35,
            sharpen=0.2,
            body_enhance=True,
            body_enhancement_denoise=0.45,
            face_enhancement_denoise=0.25,
            details=0.7,
            general_denoise=0.55,
            custom_prompt="Preserve the stone texture",
            workflow=general.WORKFLOW_NAME,
        )
        prepared = general._prepare_general_inputs(source)
        job = general._build_general_payload(
            prepared,
            workflow_debug=False,
            is_admin_user=False,
        )
        workflow = job.payload["input"]["workflow"]

        self.assertEqual(
            workflow[general.NODE_IMAGE_INPUT]["inputs"]["image"],
            prepared.image_b64,
        )
        self.assertEqual(
            workflow[general.NODE_MASK_INPUT]["inputs"]["image"],
            prepared.mask_b64,
        )
        self.assertEqual(
            workflow[general.NODE_MASK_ROUTER]["inputs"]["mask"],
            [general.NODE_MASK_ROUTE_EMPTY, 0],
        )
        self.assertEqual(
            workflow[general.NODE_SD_LORA]["inputs"]["strength_model"],
            0.7,
        )
        self.assertEqual(
            workflow[general.NODE_SD_SAMPLER]["inputs"]["denoise"],
            0.55,
        )
        self.assertEqual(
            workflow[general.NODE_FLUX_SCHEDULER]["inputs"]["denoise"],
            0.35,
        )
        self.assertEqual(
            workflow[general.NODE_FLUX_BLEND]["inputs"]["blend_factor"],
            0.2,
        )
        self.assertEqual(
            workflow[general.NODE_SAVE_IMAGE]["inputs"]["images"],
            [general.NODE_BODY_SAMPLER_2, 0],
        )


class GeneralProgressDisplayTests(unittest.TestCase):
    def test_fraction_direct_stage_has_a_provisional_total_while_running(self) -> None:
        stage = {
            "enabled": True,
            "count_mode": general.COUNT_MODE_FRACTION_DIRECT,
            "total": None,
            "done": 0,
            "started": True,
            "finished": False,
        }

        self.assertEqual(general._effective_stage_total(stage), 1)


class GeneralJobStageTests(unittest.IsolatedAsyncioTestCase):
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
        result = await general._submit_general_job(api, {"input": {}})

        self.assertTrue(result.uncertain)
        self.assertIsNone(result.job_id)
        self.assertIn("check the Jobs page", str(result.error_message))
        self.assertEqual(api.run_calls, 1)

    async def test_failed_status_returns_user_facing_event(self) -> None:
        api = _StatusAPI({"status": "FAILED", "error": "worker exploded"})

        events = [
            event
            async for event in general._poll_general_job(
                api,
                "job-1",
                background_np=np.zeros((32, 40, 3), dtype=np.uint8),
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
            async for event in general._poll_general_job(
                api,
                "job-1",
                background_np=np.zeros((32, 40, 3), dtype=np.uint8),
                state=_poll_state(),
                stream_enabled=False,
            )
        ]

        self.assertEqual(api.status_calls, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "cancelled")
        self.assertEqual(events[0].title, "Cancelled")
        self.assertIn("cancelled", events[0].message.lower())

    async def test_malformed_images_payload_is_returned_as_decode_error(self) -> None:
        result = await general._finalize_general_output(
            {"status": "COMPLETED", "output": {"images": "malformed"}},
            background_np=np.zeros((32, 40, 3), dtype=np.uint8),
            job_id="job-1",
        )

        self.assertIsNone(result.result_image)
        self.assertIn("No decodable image", str(result.error_message))


def _poll_state() -> general.GeneralPollState:
    return general.GeneralPollState(
        progress_tracker=general._init_progress_tracker(
            image_width=40,
            image_height=32,
            general_enhance=True,
            advance_details=True,
            body_enhance=True,
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
