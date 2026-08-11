"""Tests for the helpers shared by all four workflow tabs."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import workflow_ui


class RequestHeaderTests(unittest.TestCase):
    @staticmethod
    def request_with(headers):
        return SimpleNamespace(headers=headers)

    def test_reads_exact_key(self):
        request = self.request_with({"user-agent": "Firefox"})

        self.assertEqual(workflow_ui.request_header(request, "user-agent"), "Firefox")

    def test_falls_back_to_lowercase_and_title_case(self):
        self.assertEqual(
            workflow_ui.request_header(self.request_with({"user-agent": "Firefox"}), "User-Agent"),
            "Firefox",
        )
        self.assertEqual(
            workflow_ui.request_header(self.request_with({"User-Agent": "Firefox"}), "user-agent"),
            "Firefox",
        )

    def test_missing_header_returns_none(self):
        self.assertIsNone(workflow_ui.request_header(self.request_with({}), "user-agent"))

    def test_request_without_headers_returns_none(self):
        self.assertIsNone(workflow_ui.request_header(SimpleNamespace(), "user-agent"))
        self.assertIsNone(workflow_ui.request_header(SimpleNamespace(headers=None), "user-agent"))


class IsAdminIdentityTests(unittest.TestCase):
    class FakeAuthService:
        def __init__(self, roles):
            self.roles = roles
            self.lookups = []

        def get_identity(self, email):
            self.lookups.append(email)
            return SimpleNamespace(role=self.roles.get(email))

    def patched_service(self, roles):
        service = self.FakeAuthService(roles)
        patcher = patch.object(workflow_ui, "get_auth_service", lambda: service)
        patcher.start()
        self.addCleanup(patcher.stop)
        return service

    def test_admin_role_is_recognized(self):
        self.patched_service({"admin@brickvisual.com": "admin"})

        self.assertTrue(workflow_ui.is_admin_identity("admin@brickvisual.com"))

    def test_role_comparison_ignores_case_and_padding(self):
        self.patched_service({"admin@brickvisual.com": "  ADMIN  "})

        self.assertTrue(workflow_ui.is_admin_identity("admin@brickvisual.com"))

    def test_other_roles_are_not_admin(self):
        self.patched_service({"user@brickvisual.com": "user", "boss@brickvisual.com": "ex"})

        self.assertFalse(workflow_ui.is_admin_identity("user@brickvisual.com"))
        self.assertFalse(workflow_ui.is_admin_identity("boss@brickvisual.com"))

    def test_missing_role_is_not_admin(self):
        self.patched_service({"ghost@brickvisual.com": None})

        self.assertFalse(workflow_ui.is_admin_identity("ghost@brickvisual.com"))

    def test_blank_email_short_circuits_without_a_lookup(self):
        service = self.patched_service({})

        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertFalse(workflow_ui.is_admin_identity(value))

        self.assertEqual(service.lookups, [])


class SaveWorkflowDebugJsonTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.debug_dir = Path(self.temp_dir.name) / "nested" / "workflow_debug"

    def save(self, payload, *, workflow_name="wf", task_id="task-1", prefix="general"):
        return workflow_ui.save_workflow_debug_json(
            payload,
            workflow_name=workflow_name,
            task_id=task_id,
            prefix=prefix,
            debug_dir=self.debug_dir,
        )

    def test_creates_missing_directories(self):
        path = self.save({"a": 1})

        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, self.debug_dir)

    def test_unwraps_the_comfyui_workflow_from_the_runpod_envelope(self):
        payload = {"input": {"workflow": {"76": {"class_type": "LoadImage"}}, "other": "ignored"}}

        path = self.save(payload)

        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")),
            {"76": {"class_type": "LoadImage"}},
        )

    def test_keeps_payload_as_is_when_there_is_no_envelope(self):
        payload = {"prompt": "hello"}

        path = self.save(payload)

        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)

    def test_non_dict_workflow_value_is_not_unwrapped(self):
        payload = {"input": {"workflow": "not-a-dict"}}

        path = self.save(payload)

        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)

    def test_filename_carries_prefix_task_id_and_sanitized_workflow(self):
        path = self.save({}, workflow_name="flux2 klein/v2*", task_id="abc123", prefix="flux2_klein")

        self.assertTrue(path.name.startswith("flux2_klein_flux2_klein_v2_abc123_"))
        self.assertTrue(path.name.endswith(".json"))

    def test_blank_workflow_name_falls_back(self):
        for name in ("", "   ", "***", None):
            with self.subTest(name=name):
                path = self.save({}, workflow_name=name, prefix="upscaler")
                self.assertTrue(path.name.startswith("upscaler_workflow_"))

    def test_each_prefix_stays_distinct(self):
        names = {
            prefix: self.save({}, prefix=prefix).name
            for prefix in ("general", "upscaler", "reference_generator", "flux2_klein")
        }

        for prefix, name in names.items():
            self.assertTrue(name.startswith(f"{prefix}_"))

    def test_defaults_to_the_shared_debug_directory(self):
        target = Path(self.temp_dir.name) / "default-dir"
        with patch.object(workflow_ui, "WORKFLOW_DEBUG_JSON_DIR", target):
            path = workflow_ui.save_workflow_debug_json(
                {},
                workflow_name="wf",
                task_id="t",
                prefix="general",
            )

        self.assertEqual(path.parent, target)


if __name__ == "__main__":
    unittest.main()
