import express from "express";
import { getAutomationConfig, getCurrentAutomationDecision, getLiveSnapshot, saveAutomationConfig } from "../automationEngine.js";
import { canManageSchedule, canManageWorkers, getRequestRole } from "../middleware/requireAdmin.js";
import { getEndpointHealth, getEndpoints, updateEndpointConfig } from "../runpodClient.js";

export const runpodRoutes = express.Router();

const asyncHandler = (handler) => (req, res, next) => {
  Promise.resolve(handler(req, res, next)).catch(next);
};

const ensureApiKey = (res) => {
  if (process.env.RUNPOD_API_KEY) {
    return true;
  }

  res.status(500).json({
    error: "RUNPOD_API_KEY is not configured. Add it in your .env file."
  });
  return false;
};

const findEndpointById = async (endpointId) => {
  const endpoints = await getEndpoints();
  const endpoint = endpoints.find((item) => item.id === endpointId);
  return { endpoint, endpoints };
};

const getPermissions = (req) => {
  const role = getRequestRole(req);
  return {
    role,
    permissions: {
      manageWorkers: canManageWorkers(role),
      manageSchedule: canManageSchedule(role)
    }
  };
};

const scheduleConfigFields = [
  "scheduleEnabled",
  "autoscalerEnabled",
  "planAEnable",
  "startTime",
  "endTime",
  "activeWorkers",
  "startWorkers",
  "planAWorkers",
  "workingDays",
  "nightSafetyLockEnabled"
];

const clampInt = (value, fallback, min, max) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, Math.trunc(parsed)));
};

runpodRoutes.get("/session", (req, res) => {
  res.json(getPermissions(req));
});

runpodRoutes.get(
  "/endpoints",
  asyncHandler(async (req, res) => {
    if (!ensureApiKey(res)) {
      return;
    }

    const endpoints = await getEndpoints();
    res.json({ endpoints });
  })
);

runpodRoutes.get(
  "/endpoint/:endpointId/dashboard",
  asyncHandler(async (req, res) => {
    if (!ensureApiKey(res)) {
      return;
    }

    const { endpointId } = req.params;
    const { endpoint } = await findEndpointById(endpointId);

    if (!endpoint) {
      res.status(404).json({ error: "Endpoint not found." });
      return;
    }

    const health = await getEndpointHealth(endpointId);

    res.json({
      endpoint,
      health
    });
  })
);

runpodRoutes.post(
  "/endpoint/:endpointId/workers",
  asyncHandler(async (req, res) => {
    if (!ensureApiKey(res)) {
      return;
    }

    const { endpointId } = req.params;
    const activeWorkers = Number(req.body?.activeWorkers);
    const maxWorkers = Number(req.body?.maxWorkers);
    const idleTimeout = Number(req.body?.idleTimeout);
    const manualOverride = Boolean(req.body?.manualOverride);
    const durationHours = clampInt(req.body?.durationHours ?? req.body?.manualOverrideDurationHours, 1, 1, 2);

    if (!Number.isFinite(activeWorkers) || !Number.isFinite(maxWorkers) || !Number.isFinite(idleTimeout)) {
      res.status(400).json({ error: "activeWorkers, maxWorkers, and idleTimeout must be numbers." });
      return;
    }

    if (manualOverride && (activeWorkers < 1 || activeWorkers > 2)) {
      res.status(400).json({ error: "Manual reserve workers must be between 1 and 2." });
      return;
    }

    if (activeWorkers > maxWorkers) {
      res.status(400).json({ error: "activeWorkers cannot be greater than maxWorkers." });
      return;
    }

    const { endpoint } = await findEndpointById(endpointId);
    if (!endpoint) {
      res.status(404).json({ error: "Endpoint not found." });
      return;
    }

    const automation =
      req.body?.manualOverride === undefined
        ? undefined
        : saveAutomationConfig(endpointId, {
            manualOverride: {
              enabled: manualOverride,
              activeWorkers,
              durationHours
            }
          });
    const decision = manualOverride ? getCurrentAutomationDecision(endpointId) : null;
    const effectiveWorkers = decision?.shouldControlWorkers ? Number(decision.targetWorkers) : activeWorkers;
    const effectiveMaxWorkers = Math.max(effectiveWorkers, maxWorkers);
    const updated = await updateEndpointConfig(endpoint, effectiveWorkers, effectiveMaxWorkers, idleTimeout);

    res.json({
      message: "Endpoint workers updated.",
      endpoint: updated,
      automation
    });
  })
);

runpodRoutes.get("/endpoint/:endpointId/automation", (req, res) => {
  const { endpointId } = req.params;
  const automation = getAutomationConfig(endpointId);
  res.json({ automation });
});

runpodRoutes.post("/endpoint/:endpointId/automation", (req, res) => {
  const { endpointId } = req.params;
  const role = getRequestRole(req);
  const body = req.body ?? {};
  const hasManualOverride = Object.prototype.hasOwnProperty.call(body, "manualOverride");
  const hasScheduleUpdate = scheduleConfigFields.some((field) => Object.prototype.hasOwnProperty.call(body, field));

  if (hasScheduleUpdate && !canManageSchedule(role) && !hasManualOverride) {
    res.status(403).json({ error: "Executive access is required to manage Smart Worker Schedule." });
    return;
  }

  if (hasManualOverride && body.manualOverride?.enabled) {
    const activeWorkers = Number(body.manualOverride.activeWorkers);
    const durationHours = Number(body.manualOverride.durationHours);
    if (!Number.isFinite(activeWorkers) || activeWorkers < 1 || activeWorkers > 2) {
      res.status(400).json({ error: "Manual reserve workers must be between 1 and 2." });
      return;
    }
    if (!Number.isFinite(durationHours) || durationHours < 1 || durationHours > 2) {
      res.status(400).json({ error: "Manual reserve duration must be between 1 and 2 hours." });
      return;
    }
  }

  const config = canManageSchedule(role) ? body : { manualOverride: body.manualOverride };
  const automation = saveAutomationConfig(endpointId, config);
  res.json({
    message: "Automation settings saved.",
    automation
  });
});

runpodRoutes.get("/endpoint/:endpointId/live", (req, res) => {
  const { endpointId } = req.params;
  const live = getLiveSnapshot(endpointId);
  res.json(live);
});
