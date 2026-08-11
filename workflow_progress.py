from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import os
import re
from typing import Any


logger = logging.getLogger(__name__)


COMFY_LOG_PATTERN = re.compile(r"^\[comfy-log\]\[(?P<phase>[^\]]+)\]\s*(?P<message>.*)$")
NODE_PROGRESS_PATTERN = re.compile(
    r"^node=(?P<node>[^ ]+)\s+(?P<done>\d+)/(?P<total>\d+)$"
)
SAMPLER_PROGRESS_PATTERN = re.compile(
    r"node=(?P<node>[^ ]+)\s+(?P<done>\d+)/(?P<total>\d+)"
)
RUNNING_NODE_PATTERN = re.compile(
    r"^Running node (?P<node>\d+(?::\d+)?):\s*(?P<label>.+)$"
)
NODE_ID_EQUALS_PATTERN = re.compile(r"\bnode=(?P<node>\d+(?::\d+)?)\b")
NODE_ID_PREFIX_PATTERN = re.compile(r"^(?P<node>\d+(?::\d+)?)\b")
FRACTION_PATTERN = re.compile(r"^(?P<done>\d+)\s*/\s*(?P<total>\d+)$")
TOTAL_VALUE_PATTERN = re.compile(r"total\s*=\s*(?P<total>\d+)", re.IGNORECASE)
ENHANCE_ITEM_PATTERN = re.compile(
    r"^node=(?P<node>[^ ]+)\s+done=(?P<done>\d+)(?:\s+total=(?P<total>\d+))?$"
)
ENHANCE_STATE_PATTERN = re.compile(
    r"^(?:node=(?P<node>[^ ]+)\s+)?done=(?P<done>\d+)(?:\s+total=(?P<total>\d+))?$"
)
ENHANCE_STEP_PATTERN = re.compile(
    r"^node=(?P<node>[^ ]+)\s+item=(?P<item_done>\d+)(?:/(?P<item_total>\d+))?\s+step=(?P<step_done>\d+)/(?P<step_total>\d+)$"
)
ENHANCE_DONE_INLINE_PATTERN = re.compile(
    r"enhance_done=(?P<done>\d+)(?:/(?P<total>\d+))?",
    re.IGNORECASE,
)
EULER_PROGRESS_PATTERN = re.compile(r"EulerSampler:\s*(?P<pct>\d+)%\|")
QUEUE_REMAINING_PATTERN = re.compile(r"Queue remaining:\s*(?P<remaining>.+)$")

PHASE_PREPARATION = "Preparation"
PHASE_UPSCALING = "Upscaling"
PHASE_ENHANCEMENT = "Enhancement"
PHASE_WRAP_UP = "Wrap-up"
PHASE_COMPLETED = "Completed"

COUNT_MODE_CYCLE = "cycle"
COUNT_MODE_ITEM_COUNTER = "item_counter"
COUNT_MODE_FRACTION_DIRECT = "fraction_direct"

ENHANCE_FALLBACK_COMPLETE_RATIO = float(
    os.getenv("ENHANCE_FALLBACK_COMPLETE_RATIO", "0.85")
)
PROGRESS_RECONCILE_MAX_MISSING = max(
    1,
    int(os.getenv("PROGRESS_RECONCILE_MAX_MISSING", "1")),
)


@dataclass(frozen=True)
class StageSpec:
    key: str
    label: str
    weight: float = 0.0
    total: int | None = None
    enabled: bool = True
    unit_label: str = "item"
    node_id: str | None = None
    dynamic_total: bool = False
    count_mode: str = COUNT_MODE_CYCLE


def clamp_ratio(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def progress_bar(percent: int, width: int = 28) -> str:
    filled = int(round((percent / 100.0) * width))
    filled = max(0, min(width, filled))
    return f"{'█' * filled}{'░' * (width - filled)}"


def extract_node_id(text: str | None) -> str | None:
    if not text:
        return None
    stripped = text.strip()
    running_match = RUNNING_NODE_PATTERN.match(stripped)
    if running_match:
        return running_match.group("node").strip()
    equals_match = NODE_ID_EQUALS_PATTERN.search(stripped)
    if equals_match:
        return equals_match.group("node").strip()
    prefix_match = NODE_ID_PREFIX_PATTERN.match(stripped)
    if prefix_match:
        return prefix_match.group("node").strip()
    return None


class ProgressTracker(dict[str, Any]):
    def __init__(
        self,
        *,
        mode: str,
        specs: list[StageSpec],
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(state)
        self.mode = mode
        self.specs = tuple(specs)
        self._specs_by_key = {spec.key: spec for spec in specs}
        self._config = dict(config or {})

    @classmethod
    def for_reference(
        cls,
        *,
        specs: list[StageSpec],
        node_stage_hints: dict[str, str],
        node_status_hints: dict[str, str],
        node_progress_hints: dict[str, float],
        save_stage_key: str,
        save_node_id: str,
    ) -> ProgressTracker:
        first_spec = specs[0]
        stages = {
            spec.key: {
                "enabled": spec.enabled,
                "label": spec.label,
                "progress": 0.0,
                "started": False,
                "finished": False,
            }
            for spec in specs
        }
        return cls(
            mode="reference",
            specs=specs,
            state={
                "phase": first_spec.label,
                "current_stage": first_spec.key,
                "current_status": "Preparing workflow...",
                "last_node_id": None,
                "stages": stages,
            },
            config={
                "node_stage_hints": node_stage_hints,
                "node_status_hints": node_status_hints,
                "node_progress_hints": node_progress_hints,
                "save_stage_key": save_stage_key,
                "save_node_id": save_node_id,
            },
        )

    @classmethod
    def for_general(
        cls,
        *,
        specs: list[StageSpec],
        tile_columns: int,
        tile_rows: int,
        node_stage_hints: dict[str, str],
        node_status_hints: dict[str, str],
        sampler_node_to_stage: dict[str, str],
        wrap_milestones: dict[str, float],
        save_node_id: str,
        sampling_ceiling: int,
        sync_stage_keys: tuple[str, str] | None = None,
        advance_stage_key: str | None = None,
    ) -> ProgressTracker:
        stages = {
            spec.key: {
                "enabled": spec.enabled,
                "label": spec.label,
                "unit_label": spec.unit_label,
                "node_id": spec.node_id,
                "count_mode": spec.count_mode,
                "total": spec.total if spec.enabled else 0,
                "dynamic_total": spec.dynamic_total,
                "done": 0,
                "started": False,
                "finished": not spec.enabled,
                "step_done": None,
                "step_total": None,
                "last_step": None,
                "last_total": None,
                "cycle_complete": False,
                "peak_step": 0,
                "step_item": None,
                "runtime_done_events_seen": False,
            }
            for spec in specs
        }
        return cls(
            mode="general",
            specs=specs,
            state={
                "phase": PHASE_PREPARATION,
                "current_stage": None,
                "current_status": "Preparing workflow...",
                "wrap_ratio": 0.0,
                "tile_columns": tile_columns,
                "tile_rows": tile_rows,
                "tile_count": tile_columns * tile_rows,
                "stages": stages,
            },
            config={
                "node_stage_hints": node_stage_hints,
                "node_status_hints": node_status_hints,
                "sampler_node_to_stage": sampler_node_to_stage,
                "wrap_milestones": wrap_milestones,
                "save_node_id": save_node_id,
                "sampling_ceiling": sampling_ceiling,
                "sync_stage_keys": sync_stage_keys,
                "advance_stage_key": advance_stage_key,
            },
        )

    @classmethod
    def for_phases(
        cls,
        *,
        workflow_profile: dict[str, Any],
        tile_estimate: dict[str, Any] | None = None,
    ) -> ProgressTracker:
        specs = [
            StageSpec(
                "preparation",
                PHASE_PREPARATION,
                weight=float(os.getenv("PHASE_WEIGHT_PREPARATION", "12")),
            ),
            StageSpec(
                "upscaling",
                str(workflow_profile.get("upscale_label") or PHASE_UPSCALING),
                weight=float(os.getenv("PHASE_WEIGHT_UPSCALING", "46")),
            ),
            StageSpec(
                "enhancement",
                str(workflow_profile.get("enhancement_label") or PHASE_ENHANCEMENT),
                weight=float(os.getenv("PHASE_WEIGHT_ENHANCEMENT", "32")),
            ),
            StageSpec(
                "wrap_up",
                PHASE_WRAP_UP,
                weight=float(os.getenv("PHASE_WEIGHT_WRAP_UP", "10")),
            ),
        ]
        estimate = tile_estimate or {}
        return cls(
            mode="phases",
            specs=specs,
            state={
                "phase": PHASE_PREPARATION,
                "prep_ratio": 0.05,
                "upscale_done": 0,
                "upscale_total": None,
                "seedvr_frames_total": None,
                "upscale_ratio": 0.0,
                "seedvr_stage": None,
                "enhance_done": 0,
                "enhance_total": workflow_profile.get("enhancement_total_override"),
                "enhance_ratio": 0.0,
                "wrap_ratio": 0.0,
                "enhance_cycle_complete": False,
                "enhance_last_step": None,
                "enhance_last_total_steps": None,
                "enhance_peak_step": 0,
                "enhance_runtime_seen": False,
                "enhance_item_seen": False,
                "enhance_log_pass": 1,
                "enhance_log_last_step": None,
                "enhance_log_last_total": None,
                "upscale_node_id": workflow_profile.get("upscale_node_id"),
                "enhancement_node_id": workflow_profile.get("enhancement_node_id"),
                "wrap_up_node_ids": workflow_profile.get("wrap_up_node_ids", []),
                "wrap_up_milestones": workflow_profile.get("wrap_up_milestones", {}),
                "seedvr_runtime_enabled": workflow_profile.get("seedvr_runtime_enabled", False),
                "upscale_label": workflow_profile.get("upscale_label", PHASE_UPSCALING),
                "enhancement_label": workflow_profile.get(
                    "enhancement_label", PHASE_ENHANCEMENT
                ),
                "enhancement_total_from_upscale": workflow_profile.get(
                    "enhancement_total_from_upscale", True
                ),
                "enhancement_total_override": workflow_profile.get(
                    "enhancement_total_override"
                ),
                "estimated_tile_columns": estimate.get("estimated_tile_columns"),
                "estimated_tile_rows": estimate.get("estimated_tile_rows"),
                "estimated_tile_count": estimate.get("estimated_tile_count"),
                "estimated_tile_source_width": estimate.get("estimated_tile_source_width"),
                "estimated_tile_source_height": estimate.get("estimated_tile_source_height"),
                "estimated_tile_divisor": estimate.get("estimated_tile_divisor"),
                "estimated_tile_note": estimate.get("estimated_tile_note"),
            },
        )

    def observe_text(self, progress_text: str) -> None:
        if self.mode == "reference":
            self._observe_reference(progress_text)
        elif self.mode == "general":
            self._observe_general(progress_text)
        elif self.mode == "phases":
            self._observe_phases(progress_text)
        else:
            raise ValueError(f"Unknown progress tracker mode: {self.mode}")

    def overall_percent(
        self,
        *,
        completed: bool = False,
        runpod_progress: int | float | None = None,
    ) -> int:
        if completed:
            return 100
        if self.mode == "reference":
            return self._reference_overall_percent(runpod_progress=runpod_progress)
        if self.mode == "general":
            return self._general_overall_percent()
        if self.mode == "phases":
            return self._phase_overall_percent()
        raise ValueError(f"Unknown progress tracker mode: {self.mode}")

    def set_stage_progress(
        self,
        stage_key: str,
        ratio: float,
        *,
        message: str | None = None,
        node_id: str | None = None,
    ) -> None:
        if self.mode != "reference":
            raise ValueError("Direct stage progress is only available for reference trackers.")
        self._set_reference_stage_progress(
            stage_key,
            ratio,
            message=message,
            node_id=node_id,
        )

    def start_wrap(self, message: str, *, min_wrap_ratio: float | None = None) -> None:
        if self.mode != "general":
            raise ValueError("Direct wrap-up transitions are only available for general trackers.")
        self._set_general_wrap(message, min_wrap_ratio=min_wrap_ratio)

    def mark_completed(self) -> None:
        if self.mode != "general":
            raise ValueError("Completion transitions are only available for general trackers.")
        self._general_transition(None)
        self["phase"] = PHASE_COMPLETED

    def _reference_transition(self, stage_key: str) -> None:
        if stage_key not in self["stages"]:
            return
        stage_order = [spec.key for spec in self.specs]
        target_index = stage_order.index(stage_key)
        for index, existing_key in enumerate(stage_order):
            stage = self["stages"][existing_key]
            if not stage.get("enabled"):
                continue
            if index < target_index:
                stage["started"] = True
                stage["finished"] = True
                stage["progress"] = max(float(stage.get("progress") or 0.0), 1.0)

        stage = self["stages"][stage_key]
        if stage.get("enabled"):
            stage["started"] = True
            self["current_stage"] = stage_key
            self["phase"] = stage["label"]

    def _set_reference_stage_progress(
        self,
        stage_key: str,
        ratio: float,
        *,
        message: str | None = None,
        node_id: str | None = None,
    ) -> None:
        stage = self["stages"].get(stage_key)
        if not stage or not stage.get("enabled"):
            return
        self._reference_transition(stage_key)
        stage["progress"] = max(float(stage.get("progress") or 0.0), clamp_ratio(ratio))
        if stage["progress"] >= 1.0:
            stage["finished"] = True
        self["last_node_id"] = node_id or self.get("last_node_id")
        if message:
            self["current_status"] = message
        else:
            self["current_status"] = self._config["node_status_hints"].get(
                node_id or "", stage["label"]
            )

    def _set_reference_stage_from_node(self, node_id: str | None) -> None:
        if not node_id:
            return
        stage_key = self._config["node_stage_hints"].get(node_id)
        if not stage_key:
            return
        stage = self["stages"].get(stage_key)
        if not stage or not stage.get("enabled"):
            return
        self._set_reference_stage_progress(
            stage_key,
            self._config["node_progress_hints"].get(node_id, 0.05),
            message=self._config["node_status_hints"].get(node_id, stage["label"]),
            node_id=node_id,
        )

    def _reference_overall_percent(
        self,
        *,
        runpod_progress: int | float | None,
    ) -> int:
        active_specs = [spec for spec in self.specs if self["stages"][spec.key].get("enabled")]
        if not active_specs:
            fallback = int(float(runpod_progress)) if isinstance(runpod_progress, (int, float)) else 0
            return max(0, min(fallback, 99))
        total_weight = sum(float(spec.weight) for spec in active_specs)
        if total_weight <= 0:
            return 0
        weighted_ratio = sum(
            float(self["stages"][spec.key].get("progress") or 0.0) * float(spec.weight)
            for spec in active_specs
        )
        percent = int(round((weighted_ratio / total_weight) * 99))
        if isinstance(runpod_progress, (int, float)):
            percent = max(percent, min(int(float(runpod_progress)), 96))
        return max(0, min(percent, 99))

    def _observe_reference(self, progress_text: str) -> None:
        text = (progress_text or "").strip()
        if not text:
            return
        lower = text.lower()
        node_id = extract_node_id(text)
        sampler_match = SAMPLER_PROGRESS_PATTERN.search(text)
        if sampler_match:
            node_id = sampler_match.group("node").strip()
            stage_key = self._config["node_stage_hints"].get(node_id)
            done = int(sampler_match.group("done"))
            total = max(int(sampler_match.group("total")), 1)
            if stage_key:
                self._set_reference_stage_progress(
                    stage_key,
                    done / total,
                    message=f"{self._specs_by_key[stage_key].label} - step {done}/{total}",
                    node_id=node_id,
                )
                return
        running_match = RUNNING_NODE_PATTERN.match(text)
        if running_match:
            self._set_reference_stage_from_node(running_match.group("node").strip())
            return
        if node_id:
            self._set_reference_stage_from_node(node_id)
        save_stage_key = self._config["save_stage_key"]
        save_node_id = self._config["save_node_id"]
        if "execution finished" in lower:
            self._set_reference_stage_progress(
                save_stage_key,
                0.9,
                message="Execution finished. Collecting output...",
                node_id=save_node_id,
            )
            return
        if "fetching execution history" in lower or "collecting images" in lower:
            self._set_reference_stage_progress(
                save_stage_key,
                0.95,
                message="Collecting output image...",
                node_id=save_node_id,
            )
            return
        if "job completed. returning" in lower:
            self._set_reference_stage_progress(
                save_stage_key,
                0.98,
                message="Finalizing output...",
                node_id=save_node_id,
            )
            return
        fraction_match = re.search(r"(?P<done>\d+)/(?P<total>\d+)", text)
        current_stage = self["stages"].get(str(self.get("current_stage") or ""))
        if fraction_match and current_stage and current_stage.get("enabled"):
            done = int(fraction_match.group("done"))
            total = max(int(fraction_match.group("total")), 1)
            self._set_reference_stage_progress(
                str(self["current_stage"]),
                done / total,
                message=f"{self['phase']} - step {done}/{total}",
                node_id=node_id,
            )
            return
        if not node_id and lower.startswith("still running"):
            self["current_status"] = text
            return
        if not node_id and text:
            self["current_status"] = text

    @staticmethod
    def _general_effective_total(stage: dict[str, Any]) -> int:
        if not stage.get("enabled"):
            return 0
        mode = stage.get("count_mode")
        total = stage.get("total")
        if isinstance(total, int):
            return max(total, 0)
        provisional = int(stage.get("done") or 0)
        step_item = stage.get("step_item")
        if isinstance(step_item, int) and step_item > 0:
            provisional = max(provisional, step_item)
        elif stage.get("started") and not stage.get("finished"):
            if mode == COUNT_MODE_FRACTION_DIRECT:
                provisional = max(provisional, 1)
            else:
                provisional = max(provisional + (0 if stage.get("cycle_complete") else 1), 1)
        else:
            provisional = max(provisional, 1)
        return provisional

    def _general_completed_units(self, stage: dict[str, Any]) -> float:
        if not stage.get("enabled"):
            return 0.0
        mode = stage.get("count_mode")
        completed = float(stage.get("done") or 0)
        step_done = stage.get("step_done")
        step_total = stage.get("step_total")
        if mode == COUNT_MODE_ITEM_COUNTER:
            step_item = stage.get("step_item")
            if (
                stage.get("started")
                and not stage.get("finished")
                and isinstance(step_done, int)
                and isinstance(step_total, int)
                and step_total > 0
                and isinstance(step_item, int)
                and step_item > int(stage.get("done") or 0)
            ):
                completed += clamp_ratio(step_done / step_total)
            return completed
        if mode == COUNT_MODE_FRACTION_DIRECT:
            return completed
        if (
            stage.get("started")
            and not stage.get("finished")
            and isinstance(step_done, int)
            and isinstance(step_total, int)
            and step_total > 0
            and not stage.get("cycle_complete")
        ):
            completed += clamp_ratio(step_done / step_total)
        return completed

    def _general_overall_percent(self) -> int:
        enabled_stages = [
            self["stages"][spec.key]
            for spec in self.specs
            if self["stages"][spec.key].get("enabled")
        ]
        if not enabled_stages:
            wrap_ratio = clamp_ratio(self.get("wrap_ratio") or 0.0)
            if self.get("phase") == PHASE_WRAP_UP or wrap_ratio > 0:
                return max(1, min(99, int(round(wrap_ratio * 99))))
            return 0
        total_units = sum(
            max(
                int(stage["total"]),
                0,
            )
            if isinstance(stage.get("total"), int)
            else max(self._general_effective_total(stage), 1)
            for stage in enabled_stages
        )
        completed_units = sum(self._general_completed_units(stage) for stage in enabled_stages)
        sampling_ratio = clamp_ratio(completed_units / total_units) if total_units > 0 else 0.0
        wrap_ratio = clamp_ratio(self.get("wrap_ratio") or 0.0)
        sampling_ceiling = int(self._config["sampling_ceiling"])
        wrap_span = max(1, 99 - sampling_ceiling)
        sampling_percent = sampling_ratio * sampling_ceiling
        if self.get("phase") == PHASE_WRAP_UP or wrap_ratio > 0:
            percent = int(round(sampling_percent + (wrap_ratio * wrap_span)))
            if self.get("phase") == PHASE_WRAP_UP:
                percent = max(percent, min(sampling_ceiling, 99))
        else:
            percent = int(round(sampling_percent))
        return max(0, min(99, percent))

    def _general_sampling_status(self, stage: dict[str, Any]) -> str:
        effective_total = self._general_effective_total(stage)
        current_index = int(stage.get("done") or 0)
        mode = stage.get("count_mode")
        if mode == COUNT_MODE_ITEM_COUNTER:
            step_item = stage.get("step_item")
            if isinstance(step_item, int) and step_item > 0:
                current_index = max(step_item, current_index, 1)
            else:
                current_index = max(current_index, 1)
        elif mode == COUNT_MODE_FRACTION_DIRECT:
            current_index = max(current_index, 1)
        else:
            if stage.get("started") and not stage.get("finished") and not stage.get("cycle_complete"):
                current_index += 1
            current_index = max(current_index, 1)
        if effective_total > 0:
            prefix = (
                f"{stage['label']} - {stage['unit_label']} "
                f"{min(current_index, effective_total)} of {effective_total}"
            )
        else:
            prefix = f"{stage['label']} - {stage['unit_label']} {current_index}"
        step_done = stage.get("step_done")
        step_total = stage.get("step_total")
        if isinstance(step_done, int) and isinstance(step_total, int) and step_total > 0:
            prefix += f" (sampling step {step_done} of {step_total})"
        return prefix

    @staticmethod
    def _reconcile_general_cycle(
        stage: dict[str, Any],
        *,
        mark_finished: bool = False,
        near_complete_ratio: float = 0.85,
    ) -> None:
        if not stage.get("enabled"):
            return
        last_total = stage.get("last_total")
        peak_step = int(stage.get("peak_step") or 0)
        allow_near_complete = not (
            stage.get("count_mode") == COUNT_MODE_ITEM_COUNTER
            and stage.get("runtime_done_events_seen")
        )
        if (
            allow_near_complete
            and stage.get("started")
            and not stage.get("cycle_complete")
            and isinstance(last_total, int)
            and last_total > 0
            and peak_step >= max(1, int(math.ceil(last_total * near_complete_ratio)))
        ):
            stage["done"] += 1
        if mark_finished:
            if stage.get("dynamic_total"):
                stage["total"] = max(
                    int(stage.get("total") or 0), int(stage.get("done") or 0)
                )
            else:
                fixed_total = stage.get("total")
                if isinstance(fixed_total, int):
                    done_value = max(0, int(stage.get("done") or 0))
                    if fixed_total > 0 and done_value >= (fixed_total - 1):
                        stage["done"] = fixed_total
                    else:
                        stage["done"] = min(done_value, fixed_total)
            stage["finished"] = True
        fixed_total = stage.get("total")
        if isinstance(fixed_total, int) and fixed_total >= 0:
            stage["done"] = min(int(stage.get("done") or 0), fixed_total)
        stage["step_done"] = None
        stage["step_total"] = None
        stage["last_step"] = None
        stage["last_total"] = None
        stage["cycle_complete"] = False
        stage["peak_step"] = 0
        stage["step_item"] = None

    def _general_transition(self, stage_key: str | None) -> None:
        current_stage = self.get("current_stage")
        if stage_key == current_stage:
            return
        if current_stage in self["stages"]:
            self._reconcile_general_cycle(self["stages"][current_stage], mark_finished=True)
        if stage_key is None:
            self["current_stage"] = None
            self["phase"] = PHASE_WRAP_UP
            return
        stage = self["stages"].get(stage_key)
        if not stage or not stage.get("enabled"):
            return
        stage["started"] = True
        stage["finished"] = False
        self["current_stage"] = stage_key
        self["phase"] = stage["label"]

    def _mark_general_wrap(self, ratio: float) -> None:
        self["wrap_ratio"] = max(
            clamp_ratio(self.get("wrap_ratio") or 0.0), clamp_ratio(ratio)
        )

    def _set_general_wrap(
        self,
        message: str,
        *,
        min_wrap_ratio: float | None = None,
    ) -> None:
        self._general_transition(None)
        self["phase"] = PHASE_WRAP_UP
        self["current_status"] = message
        if min_wrap_ratio is not None:
            self._mark_general_wrap(min_wrap_ratio)

    def _set_general_stage_from_node(self, node_id: str | None) -> None:
        if not node_id:
            return
        wrap_milestone = self._config["wrap_milestones"].get(node_id)
        if wrap_milestone is not None:
            self._mark_general_wrap(wrap_milestone)
        if node_id == self._config["save_node_id"]:
            self._set_general_wrap(
                self._config["node_status_hints"][node_id],
                min_wrap_ratio=wrap_milestone,
            )
            return
        stage_key = self._config["node_stage_hints"].get(node_id)
        if not stage_key:
            return
        stage = self["stages"].get(stage_key)
        if not stage or not stage.get("enabled"):
            return
        self._general_transition(stage_key)
        self["current_status"] = self._config["node_status_hints"].get(
            node_id, stage["label"]
        )

    @staticmethod
    def _set_general_runtime_total(stage: dict[str, Any], total: int | None) -> None:
        if isinstance(total, int) and total > 0:
            stage["total"] = total

    def _sync_general_with_advance(self) -> None:
        sync_keys = self._config.get("sync_stage_keys")
        if not sync_keys:
            return
        general_stage = self["stages"].get(sync_keys[0])
        advance_stage = self["stages"].get(sync_keys[1])
        if (
            not general_stage
            or not advance_stage
            or not general_stage.get("enabled")
            or not advance_stage.get("enabled")
            or not general_stage.get("runtime_done_events_seen")
        ):
            return
        advance_total = advance_stage.get("total")
        general_total = general_stage.get("total")
        advance_done = max(0, int(advance_stage.get("done") or 0))
        if not isinstance(advance_total, int) or advance_total <= 0:
            return
        if not isinstance(general_total, int) or general_total <= 0 or advance_total > general_total:
            general_stage["total"] = advance_total
            general_total = advance_total
        general_stage["done"] = min(
            max(int(general_stage.get("done") or 0), advance_done), int(general_total)
        )

    def _observe_general_done(
        self,
        *,
        stage_key: str,
        done: int,
        total: int | None,
    ) -> None:
        stage = self["stages"][stage_key]
        if not stage.get("enabled"):
            return
        self._general_transition(stage_key)
        stage["started"] = True
        stage["finished"] = False
        first_runtime_event = not stage.get("runtime_done_events_seen")
        stage["runtime_done_events_seen"] = True
        self._set_general_runtime_total(stage, total)
        done_value = max(0, int(done))
        current_total = stage.get("total")
        if total is None and isinstance(current_total, int) and done_value > current_total:
            stage["total"] = done_value
        if first_runtime_event:
            stage["done"] = done_value
        else:
            stage["done"] = max(int(stage.get("done") or 0), done_value)
        fixed_total = stage.get("total")
        if isinstance(fixed_total, int) and fixed_total >= 0:
            stage["done"] = min(stage["done"], fixed_total)
        stage["step_item"] = stage["done"]
        stage["step_done"] = None
        stage["step_total"] = None
        stage["cycle_complete"] = True
        if stage_key == self._config.get("advance_stage_key"):
            self._sync_general_with_advance()
        self["current_status"] = self._general_sampling_status(stage)

    def _observe_general_item_step(
        self,
        *,
        stage_key: str,
        item_done: int,
        item_total: int | None,
        step_done: int,
        step_total: int,
    ) -> None:
        stage = self["stages"][stage_key]
        if not stage.get("enabled"):
            return
        self._general_transition(stage_key)
        stage["started"] = True
        stage["finished"] = False
        self._set_general_runtime_total(stage, item_total)
        stage["step_item"] = max(1, int(item_done))
        current_total = stage.get("total")
        if item_total is None and isinstance(current_total, int) and stage["step_item"] > current_total:
            stage["total"] = stage["step_item"]
        stage["step_done"] = max(0, int(step_done))
        stage["step_total"] = max(1, int(step_total))
        stage["last_step"] = stage["step_done"]
        stage["last_total"] = stage["step_total"]
        stage["peak_step"] = max(int(stage.get("peak_step") or 0), stage["step_done"])
        if stage["runtime_done_events_seen"]:
            stage["cycle_complete"] = (
                stage["step_item"] <= int(stage.get("done") or 0)
                and stage["step_done"] >= stage["step_total"]
            )
        else:
            stage["cycle_complete"] = stage["step_done"] >= stage["step_total"]
            if stage["cycle_complete"]:
                stage["done"] = max(int(stage.get("done") or 0), stage["step_item"])
                fixed_total = stage.get("total")
                if isinstance(fixed_total, int) and fixed_total >= 0:
                    stage["done"] = min(stage["done"], fixed_total)
        self["current_status"] = self._general_sampling_status(stage)

    def _observe_general_sampler(
        self,
        *,
        stage_key: str,
        step_done: int,
        step_total: int,
    ) -> None:
        stage = self["stages"][stage_key]
        if not stage.get("enabled"):
            return
        self._general_transition(stage_key)
        stage["started"] = True
        stage["finished"] = False
        last_total = stage.get("last_total")
        last_step = stage.get("last_step")
        if isinstance(last_total, int) and last_total > 0 and last_total != step_total:
            self._reconcile_general_cycle(stage)
            last_step = None
        if isinstance(last_step, int) and step_done < last_step:
            self._reconcile_general_cycle(stage)
        stage["step_done"] = step_done
        stage["step_total"] = step_total
        stage["last_step"] = step_done
        stage["last_total"] = step_total
        stage["peak_step"] = max(int(stage.get("peak_step") or 0), step_done)
        mode = stage.get("count_mode")
        if mode == COUNT_MODE_FRACTION_DIRECT:
            self._set_general_runtime_total(stage, step_total)
            stage["done"] = max(
                int(stage.get("done") or 0), max(0, min(step_done, step_total))
            )
            if stage_key == self._config.get("advance_stage_key"):
                self._sync_general_with_advance()
            stage["cycle_complete"] = True
        elif mode == COUNT_MODE_ITEM_COUNTER and stage.get("runtime_done_events_seen"):
            stage["cycle_complete"] = step_done >= step_total
        elif step_total > 0 and step_done >= step_total:
            if not stage.get("cycle_complete"):
                stage["done"] += 1
            stage["cycle_complete"] = True
            stage["peak_step"] = 0
        else:
            stage["cycle_complete"] = False
        if mode == COUNT_MODE_ITEM_COUNTER:
            inferred_item = int(stage.get("done") or 0)
            if not stage.get("cycle_complete"):
                inferred_item += 1
            stage["step_item"] = max(1, inferred_item)
        else:
            stage["step_item"] = None
        fixed_total = stage.get("total")
        if (
            mode != COUNT_MODE_FRACTION_DIRECT
            and isinstance(fixed_total, int)
            and fixed_total > 0
            and int(stage.get("done") or 0) > fixed_total
        ):
            stage["total"] = int(stage.get("done") or 0)
            fixed_total = stage["total"]
        if isinstance(fixed_total, int) and fixed_total >= 0:
            stage["done"] = min(int(stage.get("done") or 0), fixed_total)
        self["current_status"] = self._general_sampling_status(stage)

    def _observe_general(self, progress_text: str) -> None:
        text = progress_text.strip()
        if not text:
            return
        lower = text.lower()
        if self["phase"] == PHASE_PREPARATION:
            prep_messages = (
                ("starting job and validating input", "Starting job and validating input..."),
                ("connected to comfyui worker", "Connected to ComfyUI worker."),
                ("workflow queued", "Workflow queued. Waiting for execution..."),
                ("execution started", "Execution started."),
            )
            for marker, message in prep_messages:
                if marker in lower:
                    self["current_status"] = message
                    break
        parsed = COMFY_LOG_PATTERN.match(text)
        if parsed:
            comfy_phase = parsed.group("phase").strip().lower()
            phase_message = parsed.group("message").strip()
            if comfy_phase in {"enhance-item", "enhance-state"}:
                pattern = ENHANCE_ITEM_PATTERN if comfy_phase == "enhance-item" else ENHANCE_STATE_PATTERN
                state_match = pattern.match(phase_message)
                if state_match:
                    default_node = next(iter(self._config["sampler_node_to_stage"]))
                    node_id = (state_match.groupdict().get("node") or default_node).strip()
                    total_raw = state_match.groupdict().get("total")
                    stage_key = self._config["sampler_node_to_stage"].get(node_id)
                    if stage_key and self["stages"][stage_key].get("enabled"):
                        self._observe_general_done(
                            stage_key=stage_key,
                            done=int(state_match.group("done")),
                            total=int(total_raw) if total_raw and total_raw.isdigit() else None,
                        )
                        return
            if comfy_phase == "enhance-step":
                step_match = ENHANCE_STEP_PATTERN.match(phase_message)
                if step_match:
                    stage_key = self._config["sampler_node_to_stage"].get(step_match.group("node"))
                    if stage_key and self["stages"][stage_key].get("enabled"):
                        item_total_raw = step_match.group("item_total")
                        self._observe_general_item_step(
                            stage_key=stage_key,
                            item_done=int(step_match.group("item_done")),
                            item_total=(
                                int(item_total_raw)
                                if item_total_raw and item_total_raw.isdigit()
                                else None
                            ),
                            step_done=int(step_match.group("step_done")),
                            step_total=int(step_match.group("step_total")),
                        )
                        return
            if comfy_phase == "enhance-sample":
                node_sample = re.match(
                    r"^node=(?P<node>[^ ]+)\s+(?P<done>\d+)(?:/(?P<total>\d+))?$",
                    phase_message,
                )
                if node_sample:
                    stage_key = self._config["sampler_node_to_stage"].get(node_sample.group("node"))
                    if stage_key and self["stages"][stage_key].get("enabled"):
                        total_raw = node_sample.group("total")
                        self._observe_general_done(
                            stage_key=stage_key,
                            done=int(node_sample.group("done")),
                            total=int(total_raw) if total_raw and total_raw.isdigit() else None,
                        )
                        return
            if comfy_phase == "progress":
                node_progress = NODE_PROGRESS_PATTERN.match(phase_message)
                if node_progress:
                    stage_key = self._config["sampler_node_to_stage"].get(node_progress.group("node"))
                    if stage_key and self["stages"][stage_key].get("enabled"):
                        self._observe_general_sampler(
                            stage_key=stage_key,
                            step_done=int(node_progress.group("done")),
                            step_total=int(node_progress.group("total")),
                        )
                        return
            if comfy_phase in {"node", "executed"}:
                node_id = extract_node_id(phase_message)
                if comfy_phase == "executed":
                    stage_key = self._config["sampler_node_to_stage"].get(node_id or "")
                    if stage_key and self["stages"][stage_key].get("enabled"):
                        self._reconcile_general_cycle(self["stages"][stage_key])
                self._set_general_stage_from_node(node_id)
                return
            if comfy_phase == "execution" and "finished" in phase_message.lower():
                self._set_general_wrap(
                    "Execution finished. Collecting output...", min_wrap_ratio=0.90
                )
                return
            if comfy_phase == "status" and "queue_remaining=0" in phase_message.lower():
                self._set_general_wrap("Finalizing output...", min_wrap_ratio=0.88)
                return
        running_node = RUNNING_NODE_PATTERN.match(text)
        if running_node:
            self._set_general_stage_from_node(running_node.group("node").strip())
            return
        if lower.startswith("still running"):
            if self.get("current_status"):
                return
            self["current_status"] = "Still running..."
            return
        wrap_messages = (
            ("fetching execution history", "Preparing final output...", 0.94),
            ("processing output nodes and collecting images", "Collecting generated images...", 0.96),
            ("collecting images from node", "Collecting output image...", 0.97),
            ("job completed. returning", "Finalizing output...", 0.99),
        )
        for marker, message, ratio in wrap_messages:
            if marker in lower:
                self._set_general_wrap(message, min_wrap_ratio=ratio)
                break

    def _enhancement_is_complete(self) -> bool:
        total = self.get("enhance_total")
        try:
            total_int = int(total)
        except (TypeError, ValueError):
            return False
        return total_int > 0 and int(self.get("enhance_done") or 0) >= total_int

    def _promote_phase_wrap(
        self,
        node_id: str | None,
        wrap_up_milestones: dict[str, float],
        *,
        reason: str,
    ) -> None:
        if self.get("phase") != PHASE_ENHANCEMENT or not node_id:
            return
        enhancement_node_id = self.get("enhancement_node_id")
        if enhancement_node_id and node_id == enhancement_node_id:
            return
        if not self._enhancement_is_complete():
            return
        self["phase"] = PHASE_WRAP_UP
        self["upscale_ratio"] = max(self["upscale_ratio"], 1.0)
        self["seedvr_stage"] = None
        self["wrap_ratio"] = max(self["wrap_ratio"], wrap_up_milestones.get(node_id, 0.30))
        logger.info("Promoted to wrap-up via %s on node %s after enhancement completion.", reason, node_id)

    def _set_enhancement_total_from_upscale(self, total: Any) -> None:
        if not self.get("enhancement_total_from_upscale", True):
            return
        if self.get("enhancement_total_override") is not None:
            return
        try:
            total_int = int(total)
        except (TypeError, ValueError):
            return
        if total_int <= 0:
            return
        current_total = self.get("enhance_total")
        try:
            current_int = int(current_total)
        except (TypeError, ValueError):
            current_int = 0
        if current_int <= 0 or total_int > current_int:
            self["enhance_total"] = total_int

    @staticmethod
    def _map_done_to_total(done: int, source_total: int, target_total: int) -> int:
        if source_total <= 0 or target_total <= 0:
            return max(0, done)
        if source_total == target_total:
            return max(0, min(target_total, done))
        return max(0, min(target_total, int(round((done / source_total) * target_total))))

    @staticmethod
    def _is_near_complete(done: int, total: int) -> bool:
        if total <= 0:
            return False
        return done >= total or (total - done) <= PROGRESS_RECONCILE_MAX_MISSING

    def _reconcile_upscale(self, *, reason: str) -> None:
        total = self.get("upscale_total")
        if not isinstance(total, int) or total <= 0:
            return
        done = int(self.get("upscale_done") or 0)
        if done >= total or done <= 0 or not self._is_near_complete(done, total):
            return
        self["upscale_done"] = total
        self["upscale_ratio"] = max(float(self.get("upscale_ratio") or 0.0), 1.0)
        self._set_enhancement_total_from_upscale(total)
        logger.info(
            "Upscale completion reconciled (%s): done=%s/%s -> %s/%s",
            reason,
            done,
            total,
            total,
            total,
        )

    def _reconcile_enhancement(self, *, reason: str) -> None:
        total = self.get("enhance_total")
        if not isinstance(total, int) or total <= 0:
            return
        done = int(self.get("enhance_done") or 0)
        if done >= total or done <= 0 or not self._is_near_complete(done, total):
            return
        self["enhance_done"] = total
        self["enhance_ratio"] = 1.0
        self["enhance_runtime_seen"] = True
        self["enhance_item_seen"] = True
        logger.info(
            "Enhancement completion reconciled (%s): done=%s/%s -> %s/%s",
            reason,
            done,
            total,
            total,
            total,
        )

    def _finalize_enhancement_cycle(self, *, reason: str) -> None:
        if self.get("enhance_runtime_seen", False):
            return
        total_steps = self.get("enhance_last_total_steps")
        if not isinstance(total_steps, int) or total_steps <= 0:
            return
        peak_step = int(self.get("enhance_peak_step") or 0)
        threshold = max(
            1, int(round(total_steps * clamp_ratio(ENHANCE_FALLBACK_COMPLETE_RATIO)))
        )
        if not self.get("enhance_cycle_complete", False) and peak_step >= threshold:
            if self.get("enhance_total"):
                self["enhance_done"] = min(
                    self["enhance_done"] + 1, self["enhance_total"]
                )
            else:
                self["enhance_done"] += 1
            if self.get("enhance_total"):
                self["enhance_ratio"] = clamp_ratio(
                    self["enhance_done"] / self["enhance_total"]
                )
            logger.info(
                "Enhancement fallback increment (%s): peak=%s threshold=%s total_steps=%s enhance_done=%s",
                reason,
                peak_step,
                threshold,
                total_steps,
                self.get("enhance_done"),
            )
        self["enhance_cycle_complete"] = False
        self["enhance_peak_step"] = 0
        self["enhance_last_step"] = None
        self["enhance_last_total_steps"] = None

    def _update_enhancement_count(
        self,
        *,
        node_id: str | None,
        done: int,
        total: int | None,
        runtime_seen: bool,
    ) -> None:
        if node_id:
            self["enhancement_node_id"] = node_id
        if total and total > 0:
            self["enhance_total"] = total
        if self.get("enhance_total"):
            self["enhance_done"] = min(
                max(self["enhance_done"], done), self["enhance_total"]
            )
            self["enhance_ratio"] = clamp_ratio(
                self["enhance_done"] / self["enhance_total"]
            )
        else:
            self["enhance_done"] = max(self["enhance_done"], done)
        if runtime_seen:
            self["enhance_runtime_seen"] = True
            self["enhance_item_seen"] = True
        if self.get("phase") not in {PHASE_WRAP_UP, PHASE_COMPLETED}:
            self["phase"] = PHASE_ENHANCEMENT
            self["prep_ratio"] = 1.0
            self["upscale_ratio"] = max(self["upscale_ratio"], 1.0)
            self["seedvr_stage"] = None

    def _phase_transition_reconcile(self, node_id: str | None, *, reason: str) -> None:
        upscale_node_id = self.get("upscale_node_id")
        enhancement_node_id = self.get("enhancement_node_id")
        if self.get("phase") == PHASE_UPSCALING:
            if (not upscale_node_id) or (node_id and node_id != upscale_node_id):
                self._reconcile_upscale(reason=reason)
        if self.get("phase") == PHASE_ENHANCEMENT:
            if (not enhancement_node_id) or (node_id and node_id != enhancement_node_id):
                self._reconcile_enhancement(reason=reason)
        if (
            enhancement_node_id
            and node_id != enhancement_node_id
            and self.get("phase") == PHASE_ENHANCEMENT
        ):
            self._finalize_enhancement_cycle(reason=reason)

    def _phase_apply_node(
        self,
        node_id: str | None,
        *,
        wrap_up_node_ids: set[str],
        wrap_up_milestones: dict[str, float],
        reason: str,
    ) -> None:
        upscale_node_id = self.get("upscale_node_id")
        enhancement_node_id = self.get("enhancement_node_id")
        if upscale_node_id and node_id == upscale_node_id:
            self["phase"] = PHASE_UPSCALING
            self["prep_ratio"] = 1.0
        elif enhancement_node_id and node_id == enhancement_node_id:
            self["phase"] = PHASE_ENHANCEMENT
            self["prep_ratio"] = 1.0
            self["upscale_ratio"] = max(self["upscale_ratio"], 1.0)
            self["seedvr_stage"] = None
            if self.get("upscale_total"):
                self._set_enhancement_total_from_upscale(self["upscale_total"])
        elif node_id in wrap_up_node_ids:
            self["phase"] = PHASE_WRAP_UP
            self["prep_ratio"] = 1.0
            self["upscale_ratio"] = max(self["upscale_ratio"], 1.0)
            self["seedvr_stage"] = None
            self["wrap_ratio"] = max(self["wrap_ratio"], 0.20)
        else:
            self._promote_phase_wrap(
                node_id, wrap_up_milestones, reason=reason
            )

    def _observe_phases(self, progress_text: str) -> None:
        if self.get("enhance_total") is None and self.get("enhancement_total_override") is not None:
            self["enhance_total"] = int(self["enhancement_total_override"])
        text_lower = progress_text.lower()
        upscale_node_id = self.get("upscale_node_id")
        enhancement_node_id = self.get("enhancement_node_id")
        wrap_up_node_ids = set(self.get("wrap_up_node_ids") or [])
        wrap_up_milestones = self.get("wrap_up_milestones") or {}
        seedvr_runtime_enabled = bool(self.get("seedvr_runtime_enabled", False))
        seedvr_frames_total = self.get("seedvr_frames_total")
        if self["phase"] == PHASE_PREPARATION:
            prep_markers = (
                ("starting job and validating input", 0.10),
                ("connected to comfyui worker", 0.25),
                ("workflow queued", 0.45),
                ("execution started", 0.70),
            )
            for marker, ratio in prep_markers:
                if marker in text_lower:
                    self["prep_ratio"] = max(self["prep_ratio"], ratio)
                    break
        inline_enhance = ENHANCE_DONE_INLINE_PATTERN.search(progress_text)
        if inline_enhance:
            total_raw = inline_enhance.group("total")
            self._update_enhancement_count(
                node_id=None,
                done=int(inline_enhance.group("done")),
                total=int(total_raw) if total_raw and total_raw.isdigit() else None,
                runtime_seen=True,
            )
        parsed = COMFY_LOG_PATTERN.match(progress_text)
        if parsed:
            comfy_phase = parsed.group("phase").strip().lower()
            phase_message = parsed.group("message").strip()
            if seedvr_runtime_enabled and comfy_phase == "seedvr-frames":
                total_match = TOTAL_VALUE_PATTERN.search(phase_message)
                if total_match:
                    total_frames = int(total_match.group("total"))
                    if total_frames > 0:
                        self["seedvr_frames_total"] = total_frames
                        self["upscale_total"] = total_frames
                        self._set_enhancement_total_from_upscale(total_frames)
                        self["phase"] = PHASE_UPSCALING
                        self["prep_ratio"] = 1.0
                        seedvr_frames_total = total_frames
            elif seedvr_runtime_enabled and comfy_phase in {
                "seedvr-encode",
                "seedvr-upscale",
                "seedvr-decode",
            }:
                fraction = FRACTION_PATTERN.match(phase_message)
                if fraction:
                    idx = int(fraction.group("done"))
                    raw_total = int(fraction.group("total"))
                    if raw_total > 0:
                        canonical_total = (
                            int(seedvr_frames_total)
                            if isinstance(seedvr_frames_total, int) and seedvr_frames_total > 0
                            else raw_total
                        )
                        mapped_idx = self._map_done_to_total(idx, raw_total, canonical_total)
                        self["phase"] = PHASE_UPSCALING
                        self["prep_ratio"] = 1.0
                        self["upscale_total"] = canonical_total
                        self._set_enhancement_total_from_upscale(canonical_total)
                        if comfy_phase == "seedvr-encode":
                            self["seedvr_stage"] = "VAE encode (prep)"
                        elif comfy_phase == "seedvr-upscale":
                            self["seedvr_stage"] = "SeedVR upscale"
                            self["upscale_done"] = min(mapped_idx, canonical_total)
                            self["upscale_ratio"] = clamp_ratio(mapped_idx / canonical_total)
                        else:
                            self["seedvr_stage"] = "VAE decode (wrap-up)"
                            if int(self.get("upscale_done") or 0) >= canonical_total:
                                self["upscale_ratio"] = max(self["upscale_ratio"], 1.0)
            elif comfy_phase == "enhance-frames":
                total_match = TOTAL_VALUE_PATTERN.search(phase_message)
                if total_match and int(total_match.group("total")) > 0:
                    self["enhance_total"] = int(total_match.group("total"))
            elif comfy_phase == "enhance-node":
                node_match = re.search(r"node=(?P<node>[^ ]+)", phase_message)
                if node_match:
                    self["enhancement_node_id"] = node_match.group("node")
            elif comfy_phase in {"enhance-state", "enhance-item"}:
                pattern = ENHANCE_STATE_PATTERN if comfy_phase == "enhance-state" else ENHANCE_ITEM_PATTERN
                count_match = pattern.match(phase_message)
                if count_match:
                    total_raw = count_match.group("total")
                    self._update_enhancement_count(
                        node_id=count_match.group("node"),
                        done=int(count_match.group("done")),
                        total=int(total_raw) if total_raw and total_raw.isdigit() else None,
                        runtime_seen=True,
                    )
            elif comfy_phase == "enhance-step":
                step_match = ENHANCE_STEP_PATTERN.match(phase_message)
                if step_match:
                    item_total_raw = step_match.group("item_total")
                    self["phase"] = PHASE_ENHANCEMENT
                    self["prep_ratio"] = 1.0
                    self["upscale_ratio"] = max(self["upscale_ratio"], 1.0)
                    self["seedvr_stage"] = None
                    self["enhance_runtime_seen"] = True
                    self["enhancement_node_id"] = step_match.group("node")
                    if (
                        item_total_raw
                        and item_total_raw.isdigit()
                        and self.get("enhance_total") is None
                    ):
                        self["enhance_total"] = int(item_total_raw)
            elif comfy_phase == "enhance-sample":
                self["phase"] = PHASE_ENHANCEMENT
                self["prep_ratio"] = 1.0
                self["upscale_ratio"] = max(self["upscale_ratio"], 1.0)
                self["seedvr_stage"] = None
                self["enhance_runtime_seen"] = True
                done_value: int | None = None
                total_value: int | None = None
                fraction = FRACTION_PATTERN.match(phase_message)
                if fraction:
                    done_value = int(fraction.group("done"))
                    total_value = int(fraction.group("total"))
                elif phase_message.isdigit():
                    done_value = int(phase_message)
                if total_value is not None and total_value > 0:
                    self["enhance_total"] = total_value
                elif self.get("enhance_total") is None and self.get("upscale_total"):
                    self["enhance_total"] = self["upscale_total"]
                if done_value is not None and not self.get("enhance_item_seen", False):
                    if self.get("enhance_total"):
                        self["enhance_done"] = min(
                            max(self["enhance_done"], done_value), self["enhance_total"]
                        )
                        self["enhance_ratio"] = clamp_ratio(
                            self["enhance_done"] / self["enhance_total"]
                        )
                    else:
                        self["enhance_done"] = max(self["enhance_done"], done_value)
            if comfy_phase == "node":
                node_id = extract_node_id(phase_message)
                self._phase_transition_reconcile(node_id, reason="node-transition")
                self._phase_apply_node(
                    node_id,
                    wrap_up_node_ids=wrap_up_node_ids,
                    wrap_up_milestones=wrap_up_milestones,
                    reason="node-log",
                )
            elif comfy_phase == "progress":
                node_progress = NODE_PROGRESS_PATTERN.match(phase_message)
                if node_progress:
                    node_id = node_progress.group("node")
                    done = int(node_progress.group("done"))
                    total = int(node_progress.group("total"))
                    self._phase_transition_reconcile(node_id, reason="progress-transition")
                    if upscale_node_id and node_id == upscale_node_id and total > 0:
                        self["phase"] = PHASE_UPSCALING
                        self["prep_ratio"] = 1.0
                        if not seedvr_runtime_enabled:
                            self["upscale_ratio"] = max(
                                self["upscale_ratio"], clamp_ratio(done / total)
                            )
                    elif enhancement_node_id and node_id == enhancement_node_id and total > 0:
                        self["phase"] = PHASE_ENHANCEMENT
                        self["prep_ratio"] = 1.0
                        self["upscale_ratio"] = max(self["upscale_ratio"], 1.0)
                        self["seedvr_stage"] = None
                        if self.get("enhance_total") is None and self.get("upscale_total"):
                            self._set_enhancement_total_from_upscale(self["upscale_total"])
                        if not self.get("enhance_runtime_seen", False):
                            prev_total = self.get("enhance_last_total_steps")
                            prev_step = self.get("enhance_last_step")
                            peak_step = int(self.get("enhance_peak_step") or 0)
                            if prev_total != total:
                                self["enhance_cycle_complete"] = False
                                self["enhance_peak_step"] = done
                                peak_step = done
                            if prev_step is not None and done < prev_step:
                                self["enhance_last_total_steps"] = total
                                self._finalize_enhancement_cycle(reason="step-reset")
                                self["enhance_peak_step"] = done
                                peak_step = done
                            self["enhance_peak_step"] = max(peak_step, done)
                            if done >= total and not self.get("enhance_cycle_complete", False):
                                self["enhance_done"] += 1
                                self["enhance_cycle_complete"] = True
                                self["enhance_peak_step"] = 0
                            elif done < total:
                                self["enhance_cycle_complete"] = False
                            self["enhance_last_step"] = done
                            self["enhance_last_total_steps"] = total
                        if self.get("enhance_total"):
                            self["enhance_done"] = min(
                                self["enhance_done"], self["enhance_total"]
                            )
                            self["enhance_ratio"] = clamp_ratio(
                                self["enhance_done"] / self["enhance_total"]
                            )
                    elif node_id in wrap_up_node_ids:
                        self["phase"] = PHASE_WRAP_UP
                        self["upscale_ratio"] = max(self["upscale_ratio"], 1.0)
                        self["seedvr_stage"] = None
                        self["wrap_ratio"] = max(
                            self["wrap_ratio"], wrap_up_milestones.get(node_id, 0.20)
                        )
                    else:
                        self._promote_phase_wrap(
                            node_id, wrap_up_milestones, reason="progress-log"
                        )
            elif comfy_phase == "executed":
                node_id = extract_node_id(phase_message)
                self._phase_transition_reconcile(node_id, reason="executed-transition")
                if upscale_node_id and node_id == upscale_node_id:
                    self["phase"] = PHASE_UPSCALING
                    if self.get("upscale_total"):
                        self["upscale_done"] = self["upscale_total"]
                        self["upscale_ratio"] = 1.0
                        self._set_enhancement_total_from_upscale(self["upscale_total"])
                        self["seedvr_stage"] = "VAE decode"
                elif enhancement_node_id and node_id == enhancement_node_id:
                    self["phase"] = PHASE_ENHANCEMENT
                    self["upscale_ratio"] = max(self["upscale_ratio"], 1.0)
                    self["seedvr_stage"] = None
                    if not self.get("enhance_runtime_seen", False):
                        if not self.get("enhance_cycle_complete", False):
                            if self.get("enhance_total"):
                                self["enhance_done"] = min(
                                    self["enhance_done"] + 1, self["enhance_total"]
                                )
                            else:
                                self["enhance_done"] += 1
                        self["enhance_cycle_complete"] = False
                        self["enhance_peak_step"] = 0
                        self["enhance_last_step"] = None
                        self["enhance_last_total_steps"] = None
                    if self.get("enhance_total"):
                        self["enhance_ratio"] = clamp_ratio(
                            self["enhance_done"] / self["enhance_total"]
                        )
                elif node_id in wrap_up_node_ids:
                    self["phase"] = PHASE_WRAP_UP
                    self["upscale_ratio"] = max(self["upscale_ratio"], 1.0)
                    self["seedvr_stage"] = None
                    self["wrap_ratio"] = max(
                        self["wrap_ratio"], wrap_up_milestones.get(node_id, 0.20)
                    )
                else:
                    self._promote_phase_wrap(
                        node_id, wrap_up_milestones, reason="executed-log"
                    )
            elif comfy_phase == "execution" and "finished" in phase_message.lower():
                self._reconcile_upscale(reason="execution-finished")
                self._reconcile_enhancement(reason="execution-finished")
                self["phase"] = PHASE_WRAP_UP
                self["wrap_ratio"] = max(self["wrap_ratio"], 0.30)
            elif comfy_phase == "status" and "queue_remaining=0" in phase_message:
                self._reconcile_upscale(reason="queue-empty")
                self._reconcile_enhancement(reason="queue-empty")
                self["phase"] = PHASE_WRAP_UP
                self["wrap_ratio"] = max(self["wrap_ratio"], 0.95)
        non_structured = (
            ("fetching execution history", "fetch-history", 0.45),
            ("processing output nodes and collecting images", "process-outputs", 0.65),
            ("collecting images from node", "collect-images", 0.85),
            ("finalizing output", "finalizing-output", 0.92),
        )
        for marker, reason, ratio in non_structured:
            if marker in text_lower:
                self._reconcile_upscale(reason=reason)
                self._reconcile_enhancement(reason=reason)
                self["phase"] = PHASE_WRAP_UP
                self["wrap_ratio"] = max(self["wrap_ratio"], ratio)
                break
        running_node = RUNNING_NODE_PATTERN.match(progress_text)
        if running_node:
            node_id = running_node.group("node").strip()
            self._phase_transition_reconcile(node_id, reason="running-transition")
            self._phase_apply_node(
                node_id,
                wrap_up_node_ids=wrap_up_node_ids,
                wrap_up_milestones=wrap_up_milestones,
                reason="running-node",
            )
            if self["phase"] == PHASE_PREPARATION:
                self["prep_ratio"] = max(self["prep_ratio"], 0.80)

    def _phase_overall_percent(self) -> int:
        phase_name = self["phase"]
        prep_ratio = float(self["prep_ratio"])
        upscale_ratio = float(self["upscale_ratio"])
        enhance_ratio = float(self["enhance_ratio"])
        wrap_ratio = float(self["wrap_ratio"])
        if phase_name in {PHASE_UPSCALING, PHASE_ENHANCEMENT, PHASE_WRAP_UP, PHASE_COMPLETED}:
            prep_ratio = 1.0
        if phase_name in {PHASE_ENHANCEMENT, PHASE_WRAP_UP, PHASE_COMPLETED}:
            upscale_ratio = max(upscale_ratio, 1.0 if self.get("upscale_total") else upscale_ratio)
        if phase_name in {PHASE_WRAP_UP, PHASE_COMPLETED} and self.get("enhance_total"):
            enhance_ratio = max(
                enhance_ratio, clamp_ratio(self["enhance_done"] / self["enhance_total"])
            )
        ratios = [
            clamp_ratio(prep_ratio),
            clamp_ratio(upscale_ratio),
            clamp_ratio(enhance_ratio),
            clamp_ratio(wrap_ratio),
        ]
        weight_total = sum(spec.weight for spec in self.specs)
        if weight_total <= 0:
            return 0
        overall = sum(ratio * spec.weight for ratio, spec in zip(ratios, self.specs)) / weight_total
        return int(round(clamp_ratio(overall) * 100))

    def apply_live_text(
        self,
        progress_text: str,
        current_node: str | None,
        node_step_done: int | None,
        node_step_total: int | None,
        queue_remaining: str | None,
        live_logs: list[str],
        last_log_line: str | None,
    ) -> tuple[str | None, int | None, int | None, str | None, list[str], str | None]:
        if self.mode != "phases":
            raise ValueError("Live log application is only available for phase trackers.")
        upscale_node_id = self.get("upscale_node_id")
        enhancement_node_id = self.get("enhancement_node_id")
        upscale_label = str(self.get("upscale_label") or PHASE_UPSCALING)
        enhancement_label = str(self.get("enhancement_label") or PHASE_ENHANCEMENT)
        self.observe_text(progress_text)
        if progress_text.startswith("[comfy-log]"):
            parsed = COMFY_LOG_PATTERN.match(progress_text)
            if parsed:
                phase = parsed.group("phase").strip().lower()
                phase_message = parsed.group("message").strip()
                if phase == "node":
                    current_node = phase_message
                    node_step_done = None
                    node_step_total = None
                elif phase == "seedvr-upscale":
                    current_node = f"{upscale_node_id or 'upscale'}: {upscale_label} upscale"
                elif phase in {"seedvr-encode", "seedvr-decode"}:
                    node_step_done = None
                    node_step_total = None
                elif phase == "enhance-node":
                    node_match = re.search(r"node=(?P<node>[^ ]+)", phase_message)
                    if node_match:
                        enhance_node = node_match.group("node")
                        current_node = f"{enhance_node}: {enhancement_label} sampling"
                        self["enhancement_node_id"] = enhance_node
                    node_step_done = None
                    node_step_total = None
                elif phase == "enhance-state":
                    state_match = ENHANCE_STATE_PATTERN.match(phase_message)
                    if state_match:
                        enhance_node = state_match.group("node") or enhancement_node_id or "enhance"
                        self["enhancement_node_id"] = enhance_node
                        current_node = f"{enhance_node}: {enhancement_label} progress"
                    node_step_done = None
                    node_step_total = None
                elif phase == "enhance-item":
                    item_match = ENHANCE_ITEM_PATTERN.match(phase_message)
                    if item_match:
                        enhance_node = item_match.group("node")
                        current_node = f"{enhance_node}: {enhancement_label} item completed"
                        self["enhancement_node_id"] = enhance_node
                    node_step_done = None
                    node_step_total = None
                elif phase == "enhance-step":
                    step_match = ENHANCE_STEP_PATTERN.match(phase_message)
                    if step_match:
                        enhance_node = step_match.group("node")
                        self["enhancement_node_id"] = enhance_node
                        current_node = f"{enhance_node}: {enhancement_label} sampling"
                        node_step_done = int(step_match.group("step_done"))
                        node_step_total = int(step_match.group("step_total"))
                elif phase == "enhance-sample":
                    current_node = f"{enhancement_node_id or 'enhance'}: {enhancement_label} sampling"
                    fraction = FRACTION_PATTERN.match(phase_message)
                    if fraction:
                        node_step_done = int(fraction.group("done"))
                        node_step_total = int(fraction.group("total"))
                    elif phase_message.isdigit():
                        node_step_done = int(phase_message)
                        node_step_total = None
                elif phase == "progress":
                    node_progress = NODE_PROGRESS_PATTERN.match(phase_message)
                    if node_progress:
                        node_id = node_progress.group("node")
                        done_value = int(node_progress.group("done"))
                        total_value = int(node_progress.group("total"))
                        if (
                            self.get("seedvr_runtime_enabled", False)
                            and upscale_node_id
                            and node_id == upscale_node_id
                        ):
                            node_step_done = None
                            node_step_total = None
                        else:
                            node_step_done = done_value
                            node_step_total = total_value
                            if current_node is None or not current_node.startswith(node_id):
                                current_node = node_id
                elif phase == "status" and "queue_remaining=" in phase_message:
                    queue_remaining = phase_message.split("queue_remaining=", 1)[1].strip()
                elif phase == "execution" and "finished" in phase_message.lower():
                    current_node = "Execution finished, collecting outputs"
                    node_step_done = None
                    node_step_total = None
        else:
            running_node = RUNNING_NODE_PATTERN.match(progress_text)
            if running_node:
                current_node = f"{running_node.group('node')}: {running_node.group('label')}"
                node_step_done = None
                node_step_total = None
            queue_match = QUEUE_REMAINING_PATTERN.search(progress_text)
            if queue_match:
                queue_remaining = queue_match.group("remaining").strip()
        display_line = self._format_phase_live_log_line(progress_text)
        if display_line and display_line != last_log_line:
            live_logs.append(display_line)
            live_logs = live_logs[-12:]
            last_log_line = display_line
        return (
            current_node,
            node_step_done,
            node_step_total,
            queue_remaining,
            live_logs,
            last_log_line,
        )

    def _format_phase_live_log_line(self, progress_text: str) -> str | None:
        parsed = COMFY_LOG_PATTERN.match(progress_text)
        if not parsed:
            lower_text = progress_text.lower()
            if lower_text.startswith("still running..."):
                return "Still running..."
            if "fetching execution history" in lower_text:
                return "Preparing final output..."
            if "processing output nodes and collecting images" in lower_text:
                return "Collecting generated images..."
            if "collecting images from node" in lower_text:
                return "Collecting output image..."
            euler_match = EULER_PROGRESS_PATTERN.search(progress_text)
            if euler_match:
                return f"Sampler progress: {euler_match.group('pct')}%"
            running_node = RUNNING_NODE_PATTERN.match(progress_text)
            if running_node:
                label = running_node.group("label").strip()
                if "save image" in label.lower():
                    return "Saving final image..."
                if "vae encode" in label.lower() or "vae decode" in label.lower():
                    return None
                return f"Running {label}..."
            return progress_text
        comfy_phase = parsed.group("phase").strip().lower()
        phase_message = parsed.group("message").strip()
        enhancement_node_id = self.get("enhancement_node_id")
        upscale_node_id = self.get("upscale_node_id")
        wrap_up_node_ids = set(self.get("wrap_up_node_ids") or [])
        upscale_label = str(self.get("upscale_label") or PHASE_UPSCALING)
        enhancement_label = str(self.get("enhancement_label") or PHASE_ENHANCEMENT)
        if comfy_phase == "ws" and "receive timeout" in phase_message.lower():
            return "Still running..."
        if comfy_phase == "seedvr-frames":
            total_match = TOTAL_VALUE_PATTERN.search(phase_message)
            return (
                f"{upscale_label}: detected {int(total_match.group('total'))} frame(s)."
                if total_match
                else f"{upscale_label}: preparing frames..."
            )
        if comfy_phase == "seedvr-upscale":
            fraction = FRACTION_PATTERN.match(phase_message)
            return (
                f"{upscale_label}: upscaling {fraction.group('done')}/{fraction.group('total')}"
                if fraction
                else f"{upscale_label}: running..."
            )
        if comfy_phase in {"seedvr-encode", "seedvr-decode"}:
            return None
        if comfy_phase in {"enhance-state", "enhance-item"}:
            pattern = ENHANCE_STATE_PATTERN if comfy_phase == "enhance-state" else ENHANCE_ITEM_PATTERN
            count_match = pattern.match(phase_message)
            if count_match:
                done = int(count_match.group("done"))
                total_raw = count_match.group("total")
                if total_raw and total_raw.isdigit():
                    verb = "completed " if comfy_phase == "enhance-item" else ""
                    return f"{enhancement_label}: {verb}{done}/{int(total_raw)}"
                if comfy_phase == "enhance-item":
                    return f"{enhancement_label}: completed item {done}"
                return f"{enhancement_label}: {done} done"
            return f"{enhancement_label}: updating..."
        if comfy_phase == "enhance-step":
            step_match = ENHANCE_STEP_PATTERN.match(phase_message)
            if step_match:
                item_total_raw = step_match.group("item_total")
                step_text = (
                    f"sampling step {step_match.group('step_done')}/{step_match.group('step_total')}"
                )
                if item_total_raw and item_total_raw.isdigit():
                    return (
                        f"{enhancement_label}: item {step_match.group('item_done')}/{item_total_raw}, "
                        f"{step_text}"
                    )
                return f"{enhancement_label}: {step_text}"
            return f"{enhancement_label}: sampling..."
        if comfy_phase == "enhance-sample":
            fraction = FRACTION_PATTERN.match(phase_message)
            return (
                f"{enhancement_label}: {fraction.group('done')}/{fraction.group('total')}"
                if fraction
                else f"{enhancement_label}: sampling..."
            )
        if comfy_phase == "progress":
            node_progress = NODE_PROGRESS_PATTERN.match(phase_message)
            if node_progress:
                node_id = node_progress.group("node")
                done = int(node_progress.group("done"))
                total = int(node_progress.group("total"))
                if upscale_node_id and node_id == upscale_node_id:
                    return None
                if enhancement_node_id and node_id == enhancement_node_id:
                    enhance_total = self.get("enhance_total")
                    enhance_done = int(self.get("enhance_done") or 0)
                    if isinstance(enhance_total, int) and enhance_total > 0:
                        current_item = min(max(1, enhance_done + 1), enhance_total)
                        return (
                            f"{enhancement_label}: item {current_item}/{enhance_total}, "
                            f"sampling step {done}/{total}"
                        )
                    return f"{enhancement_label}: sampling step {done}/{total}"
                if node_id in wrap_up_node_ids:
                    return f"Finalizing output: {done}/{total}"
        if comfy_phase == "execution" and "finished" in phase_message.lower():
            return "Execution finished. Collecting output..."
        if comfy_phase == "status" and "queue_remaining=0" in phase_message:
            return "Finalizing output..."
        if comfy_phase in {"node", "executed"}:
            return None
        return progress_text
