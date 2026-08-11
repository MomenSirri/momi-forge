from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import portal_proxy


class RunpodManagementTlsTests(unittest.TestCase):
    def test_client_is_constructed_with_ca_bundle_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            ca_bundle = Path(tmp_dir) / "management-ca.pem"
            ca_bundle.write_text("test certificate", encoding="utf-8")

            with (
                patch.object(
                    portal_proxy,
                    "RUNPOD_MANAGEMENT_API_CA_BUNDLE",
                    str(ca_bundle),
                ),
                patch.object(
                    portal_proxy.httpx,
                    "AsyncClient",
                ) as client_constructor,
            ):
                portal_proxy._create_runpod_management_client()

        client_constructor.assert_called_once()
        verify_value = client_constructor.call_args.kwargs["verify"]
        self.assertEqual(verify_value, str(ca_bundle))
        self.assertIsNot(verify_value, False)

    def test_missing_ca_bundle_warns_once_and_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_bundle = Path(tmp_dir) / "missing-ca.pem"
            portal_proxy._warned_missing_ca_paths.clear()

            with (
                patch.object(
                    portal_proxy,
                    "RUNPOD_MANAGEMENT_API_CA_BUNDLE",
                    str(missing_bundle),
                ),
                self.assertLogs(portal_proxy.logger, level="WARNING") as logs,
            ):
                for _ in range(2):
                    with self.assertRaises(FileNotFoundError):
                        portal_proxy._resolve_runpod_management_ca_bundle()

        combined = "\n".join(logs.output)
        self.assertIn(
            portal_proxy.RUNPOD_MANAGEMENT_API_CA_BUNDLE_ENV,
            combined,
        )
        self.assertIn(str(missing_bundle), combined)
        self.assertEqual(len(logs.output), 1)


if __name__ == "__main__":
    unittest.main()
