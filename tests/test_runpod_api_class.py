import asyncio
import os
import unittest
from unittest.mock import Mock, patch

import requests

import runpod_api_class
from runpod_api_class import (
    RunpodAPI,
    RunpodSubmissionError,
    RunpodSubmissionUncertainError,
)


class RunpodSubmissionRetryTests(unittest.TestCase):
    def setUp(self):
        environment = {
            "RUNPOD_API_KEY": "test-key",
            "RUNPOD_POD_ID_SEED": "test-endpoint",
        }
        self.environment_patch = patch.dict(os.environ, environment, clear=False)
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)

    @staticmethod
    def _response(payload):
        response = Mock(spec=requests.Response)
        response.ok = True
        response.json.return_value = payload
        return response

    @patch.object(runpod_api_class, "sleep", return_value=None)
    @patch.object(runpod_api_class, "RUNPOD_RUN_CONNECT_ATTEMPTS", 3)
    @patch.object(runpod_api_class.requests, "request")
    def test_run_retries_connect_timeout_before_submission(self, request, _sleep):
        request.side_effect = [
            requests.exceptions.ConnectTimeout("connect timed out"),
            self._response({"id": "job-1", "status": "IN_QUEUE"}),
        ]

        result = asyncio.run(RunpodAPI().run({"input": {"prompt": "test"}}))

        self.assertEqual(result["id"], "job-1")
        self.assertEqual(request.call_count, 2)

    @patch.object(runpod_api_class, "sleep", return_value=None)
    @patch.object(runpod_api_class, "RUNPOD_RUN_CONNECT_ATTEMPTS", 3)
    @patch.object(runpod_api_class.requests, "request")
    def test_run_reports_definite_failure_after_connect_attempts(
        self, request, _sleep
    ):
        request.side_effect = requests.exceptions.ConnectTimeout("connect timed out")

        with self.assertRaisesRegex(RunpodSubmissionError, "job was not submitted"):
            asyncio.run(RunpodAPI().run({"input": {}}))

        self.assertEqual(request.call_count, 3)

    @patch.object(runpod_api_class, "sleep", return_value=None)
    @patch.object(runpod_api_class, "RUNPOD_RUN_CONNECT_ATTEMPTS", 3)
    @patch.object(runpod_api_class.requests, "request")
    def test_run_does_not_retry_ambiguous_ssl_failure(self, request, _sleep):
        request.side_effect = requests.exceptions.SSLError(
            "UNEXPECTED_EOF_WHILE_READING"
        )

        with self.assertRaisesRegex(
            RunpodSubmissionUncertainError, "may still have accepted"
        ):
            asyncio.run(RunpodAPI().run({"input": {}}))

        self.assertEqual(request.call_count, 1)

    @patch.object(runpod_api_class, "sleep", return_value=None)
    @patch.object(runpod_api_class, "RUNPOD_GET_RETRIES", 3)
    @patch.object(runpod_api_class.requests, "request")
    def test_get_requests_keep_retrying_ssl_failures(self, request, _sleep):
        request.side_effect = [
            requests.exceptions.SSLError("temporary TLS failure"),
            self._response({"status": "IN_PROGRESS"}),
        ]

        result = asyncio.run(RunpodAPI().status("job-1"))

        self.assertEqual(result["status"], "IN_PROGRESS")
        self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
