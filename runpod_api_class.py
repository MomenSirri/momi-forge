from __future__ import annotations

import asyncio
import json
import os
from time import time
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()


class RunpodAPI:
    TERMINAL_SUCCESS = {"COMPLETED"}
    TERMINAL_FAILURE = {"FAILED", "ERROR", "TIMED_OUT", "CANCELLED"}
    ACTIVE_STATES = {"IN_QUEUE", "IN_PROGRESS", "RUNNING"}

    def __init__(self, environment: str = "SEED"):
        self.api_key = os.getenv("RUNPOD_API_KEY")
        pod_id_key = f"RUNPOD_POD_ID_{environment.upper()}"
        self.pod_id = os.getenv(pod_id_key)
        self.cancel_requested = False
        self.base_url = f"https://api.runpod.ai/v2/{self.pod_id}"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        if not self.api_key:
            raise ValueError("RUNPOD_API_KEY is missing from the environment.")
        if not self.pod_id:
            raise ValueError(
                f"Pod ID for environment '{environment}' is missing. Expected env var: {pod_id_key}"
            )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        loop = asyncio.get_running_loop()

        def _do_request() -> dict[str, Any]:
            response = requests.request(
                method=method,
                url=url,
                headers=self.headers,
                json=json_body,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()

        return await loop.run_in_executor(None, _do_request)

    async def get_json(self, path: str) -> dict[str, Any]:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)

    async def run(self, json_data: dict[str, Any]) -> dict[str, Any]:
        return await self._request_json("POST", "/run", json_body=json_data, timeout=60)

    async def status(self, job_id: str) -> dict[str, Any]:
        return await self._request_json("GET", f"/status/{job_id}", timeout=30)

    async def cancel(self, job_id: str) -> dict[str, Any]:
        return await self._request_json("POST", f"/cancel/{job_id}", timeout=30)

    async def check_health(self) -> dict[str, Any]:
        try:
            result = await self._request_json("GET", "/health", timeout=15)
            workers = result.get("workers", {}) or {}
            jobs = result.get("jobs", {}) or {}
            return {
                "ok": True,
                "raw": result,
                "idle_workers": workers.get("idle"),
                "running_workers": workers.get("running"),
                "jobs_in_queue": jobs.get("inQueue"),
                "jobs_in_progress": jobs.get("inProgress"),
            }
        except Exception as err:
            return {"ok": False, "error": str(err)}

    def request_cancel(self) -> None:
        self.cancel_requested = True

    def _extract_error_message(self, status_response: dict[str, Any]) -> str:
        parts: list[str] = []
        state = (status_response.get("status") or "UNKNOWN").upper()
        parts.append(f"RunPod status: {state}")

        for key in ("error", "message"):
            value = status_response.get(key)
            if value:
                parts.append(str(value))

        output = status_response.get("output") or {}
        if isinstance(output, dict):
            for key in ("error", "message"):
                value = output.get(key)
                if value:
                    parts.append(str(value))

            for key in ("details", "errors"):
                value = output.get(key)
                if isinstance(value, list):
                    parts.extend(str(item) for item in value if item)
                elif value:
                    parts.append(str(value))

        seen: set[str] = set()
        deduped: list[str] = []
        for item in parts:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        return "\n".join(deduped)

    def _extract_useful_output(self, status_response: dict[str, Any]) -> str:
        output = status_response.get("output") or {}

        if isinstance(output, dict) and output.get("images"):
            return json.dumps(status_response, indent=2)

        if isinstance(output, dict) and "message" in output:
            message = output["message"]
            if isinstance(message, list):
                return "\n".join(str(item) for item in message)
            return str(message)

        return json.dumps(status_response, indent=2)

    async def query_json(
        self,
        json_data: dict[str, Any],
        progress=None,
        poll_interval: float = 1.0,
        max_polls: int = 1800,
    ) -> str:
        run_response = await self.run(json_data)
        job_id = run_response.get("id")
        if not job_id:
            raise ValueError(f"No job ID returned from /run: {run_response}")

        self.cancel_requested = False
        start_time = time()

        if progress is not None:
            progress(0, desc="Starting...")

        for i in range(max_polls):
            if self.cancel_requested:
                await self.cancel(job_id)
                return "__cancelled__"

            status_response = await self.status(job_id)
            state = (status_response.get("status") or "").upper()

            if progress is not None:
                pct = status_response.get("progress")
                desc = state or "UNKNOWN"
                if pct is not None:
                    desc += f" ({pct}%)"
                progress(min((i + 1) / max_polls, 0.99), desc=desc)

            if state in self.TERMINAL_SUCCESS:
                return self._extract_useful_output(status_response)

            if state in self.TERMINAL_FAILURE:
                return json.dumps(
                    {
                        "error": self._extract_error_message(status_response),
                        "job_id": job_id,
                        "status": state,
                        "raw": status_response,
                        "elapsed_seconds": round(time() - start_time, 2),
                    },
                    indent=2,
                )

            await asyncio.sleep(poll_interval)

        try:
            await self.cancel(job_id)
        except Exception:
            pass

        return json.dumps(
            {
                "error": f"Polling timed out after {max_polls} polls.",
                "job_id": job_id,
                "status": "CLIENT_TIMEOUT",
                "elapsed_seconds": round(time() - start_time, 2),
            },
            indent=2,
        )
