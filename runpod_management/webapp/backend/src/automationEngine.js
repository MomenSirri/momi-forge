import fs from "fs";
import path from "path";
import { config } from "./config.js";
import { getEndpointHealth, getEndpoints, updateEndpointConfig } from "./runpodClient.js";

const MAX_HISTORY_POINTS = 120;
const DEFAULT_WORKING_DAYS = [1, 2, 3, 4, 5];
const DAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const STORAGE_PATH = path.resolve(process.cwd(), "data", "automation-state.json");

const stateByEndpoint = new Map();
const diagnosticsByEndpoint = new Map();
const eventsByEndpoint = new Map();
const historyByEndpoint = new Map();

let loopStarted = false;
let loopRunning = false;
let persistenceLoadFailed = false;

const hhmmFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: config.timezone,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false
});

const clockFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: config.timezone,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false
});

const weekdayFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: config.timezone,
  weekday: "short"
});

const dateKeyFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: config.timezone,
  year: "numeric",
  month: "2-digit",
  day: "2-digit"
});

const getDefaultAutomationState = () => ({
  scheduleEnabled: false,
  activeWorkers: 1,
  startTime: "09:00",
  endTime: "18:00",
  workingDays: [...DEFAULT_WORKING_DAYS],
  nightSafetyLockEnabled: true,
  manualOverride: {
    enabled: false,
    activeWorkers: 0,
    durationHours: 1,
    expiresAt: null,
    updatedAt: null
  },
  lastAppliedState: "none",
  updatedAt: null
});

const normalizeInt = (value, fallback) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.trunc(parsed);
};

const clampInt = (value, fallback, min, max) => Math.min(max, Math.max(min, normalizeInt(value, fallback)));

const normalizeTime = (value, fallback) => {
  if (typeof value !== "string") {
    return fallback;
  }
  return /^\d{2}:\d{2}$/.test(value) ? value : fallback;
};

const normalizeWorkingDays = (value, fallback = DEFAULT_WORKING_DAYS) => {
  if (!Array.isArray(value)) {
    return [...fallback];
  }

  const days = [...new Set(value.map((day) => normalizeInt(day, -1)).filter((day) => day >= 1 && day <= 5))];
  return days.length ? days.sort((a, b) => a - b) : [...fallback];
};

const normalizeManualOverride = (value, fallback = getDefaultAutomationState().manualOverride) => {
  const enabled = Boolean(value?.enabled ?? fallback.enabled);
  const durationHours = clampInt(value?.durationHours, fallback.durationHours ?? 1, 1, 2);
  const updatedAt = value?.updatedAt ?? fallback.updatedAt ?? null;
  const updatedAtMs = Date.parse(updatedAt ?? "");
  const fallbackExpiresAt =
    enabled && Number.isFinite(updatedAtMs) && !value?.expiresAt
      ? new Date(updatedAtMs + durationHours * 60 * 60 * 1000).toISOString()
      : fallback.expiresAt ?? null;

  return {
    enabled,
    activeWorkers: enabled ? clampInt(value?.activeWorkers, fallback.activeWorkers || 1, 1, 2) : 0,
    durationHours,
    expiresAt: value?.expiresAt ?? fallbackExpiresAt,
    updatedAt
  };
};

const normalizeAutomationState = (value = {}) => {
  const defaults = getDefaultAutomationState();
  const scheduleEnabled = value.scheduleEnabled ?? value.autoscalerEnabled ?? value.planAEnable ?? defaults.scheduleEnabled;
  const activeWorkers = value.activeWorkers ?? value.startWorkers ?? value.planAWorkers ?? defaults.activeWorkers;

  return {
    scheduleEnabled: Boolean(scheduleEnabled),
    activeWorkers: Math.max(0, normalizeInt(activeWorkers, defaults.activeWorkers)),
    startTime: normalizeTime(value.startTime ?? value.planATime, defaults.startTime),
    endTime: normalizeTime(value.endTime ?? value.planAEndTime, defaults.endTime),
    workingDays: normalizeWorkingDays(value.workingDays, defaults.workingDays),
    nightSafetyLockEnabled: Boolean(value.nightSafetyLockEnabled ?? defaults.nightSafetyLockEnabled),
    manualOverride: normalizeManualOverride(value.manualOverride, defaults.manualOverride),
    lastAppliedState: value.lastAppliedState ?? defaults.lastAppliedState,
    updatedAt: value.updatedAt ?? defaults.updatedAt
  };
};

const getClock = (date = new Date()) => clockFormatter.format(date);

const getHourMinute = (date = new Date()) => hhmmFormatter.format(date);

const getDateKey = (date = new Date()) => {
  const parts = Object.fromEntries(dateKeyFormatter.formatToParts(date).map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
};

const getDayIndex = (date = new Date()) => {
  const dayLabel = weekdayFormatter.format(date);
  return DAY_LABELS.indexOf(dayLabel);
};

const readPersistedState = () => {
  if (!fs.existsSync(STORAGE_PATH)) {
    return;
  }

  try {
    const parsed = JSON.parse(fs.readFileSync(STORAGE_PATH, "utf8"));
    for (const [endpointId, endpointState] of Object.entries(parsed?.endpoints ?? {})) {
      stateByEndpoint.set(endpointId, normalizeAutomationState(endpointState));
    }
    console.log(`[Automation] Loaded schedules from ${STORAGE_PATH}`);
  } catch (error) {
    persistenceLoadFailed = true;
    console.error(`[Automation] Failed to load persisted schedules: ${error.message}`);
  }
};

const persistState = () => {
  const payload = {
    version: 1,
    updatedAt: new Date().toISOString(),
    endpoints: Object.fromEntries(stateByEndpoint.entries())
  };

  fs.mkdirSync(path.dirname(STORAGE_PATH), { recursive: true });
  fs.writeFileSync(STORAGE_PATH, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  persistenceLoadFailed = false;
};

readPersistedState();

const ensureEndpointState = (endpointId) => {
  if (!stateByEndpoint.has(endpointId)) {
    stateByEndpoint.set(endpointId, getDefaultAutomationState());
  }
  return stateByEndpoint.get(endpointId);
};

const withCompatibilityFields = (state) => {
  const safeState = normalizeAutomationState(state);

  return {
    ...safeState,
    autoscalerEnabled: safeState.scheduleEnabled,
    startWorkers: safeState.activeWorkers,
    planAEnable: safeState.scheduleEnabled,
    planATime: safeState.startTime,
    planAEndTime: safeState.endTime,
    planAWorkers: safeState.activeWorkers
  };
};

const addLog = (endpointId, message) => {
  const fullMessage = `[${getClock()}] ${message}`;
  const existing = eventsByEndpoint.get(endpointId) ?? [];
  existing.unshift(fullMessage);
  eventsByEndpoint.set(endpointId, existing.slice(0, 24));
  console.log(`[Automation][${endpointId}] ${fullMessage}`);
};

const isInsideWindow = (startTime, endTime, currentTime) => {
  if (startTime === endTime) {
    return false;
  }

  if (startTime <= endTime) {
    return startTime <= currentTime && currentTime < endTime;
  }

  return currentTime >= startTime || currentTime < endTime;
};

const getManualResetReason = (automationState, date = new Date()) => {
  if (!automationState.manualOverride.enabled) {
    return "";
  }

  const expiresAtMs = Date.parse(automationState.manualOverride.expiresAt ?? "");
  if (Number.isFinite(expiresAtMs) && expiresAtMs <= date.getTime()) {
    return "Manual reservation duration ended";
  }

  if (!automationState.nightSafetyLockEnabled) {
    const updatedAtMs = Date.parse(automationState.manualOverride.updatedAt ?? "");
    if (Number.isFinite(updatedAtMs) && getDateKey(new Date(updatedAtMs)) !== getDateKey(date)) {
      return "Manual reservation reset after midnight";
    }
  }

  return "";
};

const clearManualOverride = (automationState) => {
  automationState.manualOverride = normalizeManualOverride({
    enabled: false,
    activeWorkers: 0,
    durationHours: automationState.manualOverride.durationHours,
    expiresAt: null,
    updatedAt: new Date().toISOString()
  });
};

const appendHistory = (endpointId, workers) => {
  const entries = historyByEndpoint.get(endpointId) ?? [];
  entries.push({
    time: getClock(),
    idle: workers.idle ?? 0,
    running: workers.running ?? 0,
    initializing: workers.initializing ?? 0
  });

  if (entries.length > MAX_HISTORY_POINTS) {
    entries.splice(0, entries.length - MAX_HISTORY_POINTS);
  }

  historyByEndpoint.set(endpointId, entries);
};

const getScheduleDecision = (automationState, date = new Date(), context = {}) => {
  const currentTime = getHourMinute(date);

  if (persistenceLoadFailed) {
    return {
      shouldControlWorkers: true,
      targetWorkers: 0,
      scheduleStatus: "Schedule Error",
      inWindow: false,
      reason: "Schedule state could not be read; workers forced to 0"
    };
  }

  if (automationState.nightSafetyLockEnabled && isInsideWindow("20:00", "09:00", currentTime)) {
    return {
      shouldControlWorkers: true,
      targetWorkers: 0,
      scheduleStatus: "Night Safety Lock: Active",
      inWindow: false,
      reason: "Workers forced to 0 until 9:00 AM"
    };
  }

  if (automationState.manualOverride.enabled) {
    return {
      shouldControlWorkers: true,
      targetWorkers: automationState.manualOverride.activeWorkers,
      scheduleStatus: "Manual Reserve",
      inWindow: false,
      reason: automationState.manualOverride.expiresAt
        ? `Manual reservation active until ${getClock(new Date(automationState.manualOverride.expiresAt))}`
        : "Manual reservation is active"
    };
  }

  if (context.manualResetReason && !automationState.scheduleEnabled) {
    return {
      shouldControlWorkers: true,
      targetWorkers: 0,
      scheduleStatus: "Manual Reserve Ended",
      inWindow: false,
      reason: context.manualResetReason
    };
  }

  if (!automationState.scheduleEnabled) {
    return {
      shouldControlWorkers: false,
      targetWorkers: null,
      scheduleStatus: "Inactive",
      inWindow: false,
      reason: "Schedule disabled"
    };
  }

  const dayIndex = getDayIndex(date);

  if (dayIndex === 0 || dayIndex === 6) {
    return {
      shouldControlWorkers: true,
      targetWorkers: 0,
      scheduleStatus: "Weekend disabled",
      inWindow: false,
      reason: "Weekend safety shutdown"
    };
  }

  if (!automationState.workingDays.includes(dayIndex)) {
    return {
      shouldControlWorkers: true,
      targetWorkers: 0,
      scheduleStatus: "Outside working hours",
      inWindow: false,
      reason: `${DAY_LABELS[dayIndex]} is not selected`
    };
  }

  const inWindow = isInsideWindow(automationState.startTime, automationState.endTime, currentTime);
  return {
    shouldControlWorkers: true,
    targetWorkers: inWindow ? automationState.activeWorkers : 0,
    scheduleStatus: inWindow ? "Active" : "Outside working hours",
    inWindow,
    reason: inWindow ? "Inside selected schedule" : "Outside selected schedule"
  };
};

const updateDiagnostics = (endpointId, endpointName, automationState, runtime) => {
  diagnosticsByEndpoint.set(endpointId, {
    timezone: config.timezone,
    currentTime: getHourMinute(),
    currentDay: DAY_LABELS[getDayIndex()] ?? "Unknown",
    endpointName,
    connected: runtime.connected,
    scheduleEnabled: automationState.scheduleEnabled,
    nightSafetyLockEnabled: automationState.nightSafetyLockEnabled,
    nightSafetyLockActive: runtime.scheduleStatus === "Night Safety Lock: Active",
    autoscalerEnabled: automationState.scheduleEnabled,
    autoscalerActive: runtime.scheduleStatus === "Active",
    manualOverrideActive: automationState.manualOverride.enabled,
    manualReserveWorkers: automationState.manualOverride.activeWorkers,
    manualReserveDurationHours: automationState.manualOverride.durationHours,
    manualReserveExpiresAt: automationState.manualOverride.expiresAt,
    scheduleStatus: runtime.scheduleStatus,
    scheduleReason: runtime.scheduleReason,
    targetWorkers: runtime.targetWorkers,
    inWindow: runtime.inWindow,
    lastError: runtime.lastError,
    lastAppliedState: automationState.lastAppliedState
  });
};

const applyWorkerTarget = async (endpointData, targetWorkers, reason) => {
  const currentMin = endpointData.workersMin ?? 0;
  if (currentMin === targetWorkers) {
    return false;
  }

  const idleTimeout = endpointData.idleTimeout ?? 5;
  const maxWorkers = Math.max(targetWorkers, endpointData.workersMax ?? 0);
  await updateEndpointConfig(endpointData, targetWorkers, maxWorkers, idleTimeout);
  endpointData.workersMin = targetWorkers;
  endpointData.workersMax = maxWorkers;
  addLog(endpointData.id, `${reason}: set reserve workers to ${targetWorkers}.`);
  return true;
};

const applySchedule = async (endpointData, automationState, decision) => {
  if (!decision.shouldControlWorkers) {
    automationState.lastAppliedState = decision.scheduleStatus;
    return;
  }

  const targetWorkers = Math.max(0, normalizeInt(decision.targetWorkers, 0));
  await applyWorkerTarget(endpointData, targetWorkers, decision.scheduleStatus);
  automationState.lastAppliedState = decision.scheduleStatus;
};

const runAutomationCycle = async () => {
  const endpoints = await getEndpoints();

  for (const endpointData of endpoints) {
    const endpointId = endpointData.id;
    const automationState = ensureEndpointState(endpointId);
    const cycleDate = new Date();
    const manualResetReason = getManualResetReason(automationState, cycleDate);
    if (manualResetReason) {
      clearManualOverride(automationState);
      persistState();
      addLog(endpointId, `${manualResetReason}; manual reserve cleared.`);
    }
    let decision = getScheduleDecision(automationState, cycleDate, { manualResetReason });
    const runtime = {
      connected: false,
      scheduleStatus: decision.scheduleStatus,
      scheduleReason: decision.reason,
      targetWorkers: decision.targetWorkers,
      inWindow: decision.inWindow,
      lastError: ""
    };

    try {
      try {
        const health = await getEndpointHealth(endpointId);
        const workers = health.workers ?? {};
        runtime.connected = true;
        appendHistory(endpointId, workers);
      } catch (error) {
        runtime.lastError = `Health check failed: ${error.message}`;
        addLog(endpointId, runtime.lastError);
      }

      try {
        const nextDate = new Date();
        const nextManualResetReason = getManualResetReason(automationState, nextDate);
        if (nextManualResetReason) {
          clearManualOverride(automationState);
          persistState();
          addLog(endpointId, `${nextManualResetReason}; manual reserve cleared.`);
        }
        decision = getScheduleDecision(automationState, nextDate, { manualResetReason: nextManualResetReason || manualResetReason });
        runtime.scheduleStatus = decision.scheduleStatus;
        runtime.scheduleReason = decision.reason;
        runtime.targetWorkers = decision.targetWorkers;
        runtime.inWindow = decision.inWindow;
        await applySchedule(endpointData, automationState, decision);
      } catch (error) {
        runtime.lastError = `Schedule update failed: ${error.message}`;
        addLog(endpointId, runtime.lastError);

        if ((automationState.scheduleEnabled || automationState.nightSafetyLockEnabled) && (endpointData.workersMin ?? 0) !== 0) {
          try {
            await applyWorkerTarget(endpointData, 0, "Safety fallback after schedule error");
            runtime.targetWorkers = 0;
            runtime.scheduleStatus = "Outside working hours";
            runtime.scheduleReason = "Safety fallback after schedule error";
          } catch (fallbackError) {
            runtime.lastError = `Safety fallback failed: ${fallbackError.message}`;
            addLog(endpointId, runtime.lastError);
          }
        }
      }
    } catch (error) {
      runtime.lastError = `Automation error: ${error.message}`;
      addLog(endpointId, runtime.lastError);
    } finally {
      updateDiagnostics(endpointId, endpointData.name, automationState, runtime);
    }
  }
};

export const startAutomationLoop = () => {
  if (loopStarted) {
    return;
  }

  loopStarted = true;
  console.log("[Automation] Background schedule loop started.");

  const tick = async () => {
    if (loopRunning) {
      return;
    }

    loopRunning = true;
    try {
      await runAutomationCycle();
    } catch (error) {
      console.error("[Automation] Loop error:", error.message);
    } finally {
      loopRunning = false;
    }
  };

  tick();
  setInterval(tick, config.pollIntervalMs);
};

export const getAutomationConfig = (endpointId) => withCompatibilityFields(ensureEndpointState(endpointId));

export const getCurrentAutomationDecision = (endpointId) => getScheduleDecision(ensureEndpointState(endpointId));

export const saveAutomationConfig = (endpointId, partialConfig) => {
  const current = getAutomationConfig(endpointId);
  const now = new Date();
  const nextScheduleEnabled =
    partialConfig.scheduleEnabled ?? partialConfig.autoscalerEnabled ?? partialConfig.planAEnable ?? current.scheduleEnabled;
  const nextActiveWorkers =
    partialConfig.activeWorkers ?? partialConfig.startWorkers ?? partialConfig.planAWorkers ?? current.activeWorkers;
  const nextManualOverride =
    partialConfig.manualOverride !== undefined
      ? (() => {
          const enabled = Boolean(partialConfig.manualOverride?.enabled);
          const durationHours = clampInt(
            partialConfig.manualOverride?.durationHours,
            current.manualOverride.durationHours ?? 1,
            1,
            2
          );
          return {
            ...current.manualOverride,
            ...partialConfig.manualOverride,
            enabled,
            activeWorkers: enabled ? clampInt(partialConfig.manualOverride?.activeWorkers, 1, 1, 2) : 0,
            durationHours,
            expiresAt: enabled ? new Date(now.getTime() + durationHours * 60 * 60 * 1000).toISOString() : null,
            updatedAt: now.toISOString()
          };
        })()
      : current.manualOverride;

  const updated = normalizeAutomationState({
    ...current,
    scheduleEnabled: nextScheduleEnabled,
    activeWorkers: nextActiveWorkers,
    startTime: partialConfig.startTime ?? partialConfig.planATime ?? current.startTime,
    endTime: partialConfig.endTime ?? partialConfig.planAEndTime ?? current.endTime,
    workingDays: partialConfig.workingDays ?? current.workingDays,
    nightSafetyLockEnabled: partialConfig.nightSafetyLockEnabled ?? current.nightSafetyLockEnabled,
    manualOverride: nextManualOverride,
    lastAppliedState: "none",
    updatedAt: now.toISOString()
  });

  stateByEndpoint.set(endpointId, updated);
  persistState();

  if (partialConfig.manualOverride !== undefined) {
    addLog(
      endpointId,
      updated.manualOverride.enabled
        ? `Manual reserve enabled: ${updated.manualOverride.activeWorkers} worker${updated.manualOverride.activeWorkers === 1 ? "" : "s"} for ${updated.manualOverride.durationHours} hour${updated.manualOverride.durationHours === 1 ? "" : "s"}.`
        : "Manual reserve cleared; schedule control resumed."
    );
  } else {
    addLog(endpointId, updated.scheduleEnabled ? "Worker schedule saved and enabled." : "Worker schedule saved but inactive.");
  }

  return withCompatibilityFields(updated);
};

export const getLiveSnapshot = (endpointId) => {
  const automationState = ensureEndpointState(endpointId);
  const decision = getScheduleDecision(automationState);

  return {
    diagnostics:
      diagnosticsByEndpoint.get(endpointId) ?? {
        timezone: config.timezone,
        currentTime: getHourMinute(),
        currentDay: DAY_LABELS[getDayIndex()] ?? "Unknown",
        connected: false,
        scheduleEnabled: automationState.scheduleEnabled,
        nightSafetyLockEnabled: automationState.nightSafetyLockEnabled,
        nightSafetyLockActive: decision.scheduleStatus === "Night Safety Lock: Active",
        autoscalerEnabled: automationState.scheduleEnabled,
        autoscalerActive: decision.scheduleStatus === "Active",
        manualOverrideActive: automationState.manualOverride.enabled,
        manualReserveWorkers: automationState.manualOverride.activeWorkers,
        manualReserveDurationHours: automationState.manualOverride.durationHours,
        manualReserveExpiresAt: automationState.manualOverride.expiresAt,
        scheduleStatus: decision.scheduleStatus,
        scheduleReason: decision.reason,
        targetWorkers: decision.targetWorkers,
        inWindow: decision.inWindow,
        lastError: "",
        lastAppliedState: automationState.lastAppliedState
      },
    events: eventsByEndpoint.get(endpointId) ?? ["Waiting for background schedule sync..."],
    history: historyByEndpoint.get(endpointId) ?? []
  };
};
