import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Area, AreaChart, ResponsiveContainer, Tooltip } from "recharts";
import { fetchAutomation, fetchDashboard, fetchEndpoints, fetchLive, fetchSession, saveAutomation, updateWorkers } from "../../api";

const defaultAutomation = {
  scheduleEnabled: false,
  autoscalerEnabled: false,
  startTime: "09:00",
  endTime: "18:00",
  activeWorkers: 1,
  startWorkers: 1,
  workingDays: [1, 2, 3, 4, 5],
  nightSafetyLockEnabled: true,
  manualOverride: {
    enabled: false,
    activeWorkers: 0,
    durationHours: 1,
    expiresAt: null,
    updatedAt: null
  },
  lastAppliedState: "none"
};

const weekdays = [
  { id: 1, short: "Mon", label: "Monday" },
  { id: 2, short: "Tue", label: "Tuesday" },
  { id: 3, short: "Wed", label: "Wednesday" },
  { id: 4, short: "Thu", label: "Thursday" },
  { id: 5, short: "Fri", label: "Friday" },
  { id: 6, short: "Sat", label: "Saturday", disabled: true },
  { id: 0, short: "Sun", label: "Sunday", disabled: true }
];

const defaultLive = {
  diagnostics: null,
  events: [],
  history: []
};

const toSafeInt = (value, fallback = 0) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.trunc(parsed) : fallback;
};

const clampInt = (value, fallback, min, max) => Math.min(max, Math.max(min, toSafeInt(value, fallback)));

const normalizeWorkingDays = (value) => {
  if (!Array.isArray(value)) {
    return [1, 2, 3, 4, 5];
  }
  const days = [...new Set(value.map((day) => toSafeInt(day, -1)).filter((day) => day >= 1 && day <= 5))];
  return days.length ? days.sort((a, b) => a - b) : [1, 2, 3, 4, 5];
};

const normalizeAutomation = (automation = {}) => {
  const scheduleEnabled = Boolean(automation.scheduleEnabled ?? automation.autoscalerEnabled ?? automation.planAEnable ?? false);
  const activeWorkers = Math.max(0, toSafeInt(automation.activeWorkers ?? automation.startWorkers ?? automation.planAWorkers, 1));

  return {
    scheduleEnabled,
    autoscalerEnabled: scheduleEnabled,
    startTime: typeof (automation.startTime ?? automation.planATime) === "string" ? automation.startTime ?? automation.planATime : "09:00",
    endTime: typeof (automation.endTime ?? automation.planAEndTime) === "string" ? automation.endTime ?? automation.planAEndTime : "18:00",
    activeWorkers,
    startWorkers: activeWorkers,
    workingDays: normalizeWorkingDays(automation.workingDays),
    nightSafetyLockEnabled: Boolean(automation.nightSafetyLockEnabled ?? true),
    manualOverride: {
      enabled: Boolean(automation.manualOverride?.enabled ?? false),
      activeWorkers: automation.manualOverride?.enabled ? clampInt(automation.manualOverride?.activeWorkers, 1, 1, 5) : 0,
      durationHours: clampInt(automation.manualOverride?.durationHours, 1, 1, 2),
      expiresAt: automation.manualOverride?.expiresAt ?? null,
      updatedAt: automation.manualOverride?.updatedAt ?? null
    },
    lastAppliedState: automation.lastAppliedState ?? "none"
  };
};

const createCardState = (endpoint) => ({
  endpoint: endpoint ?? null,
  health: null,
  automation: defaultAutomation,
  live: defaultLive,
  manualReserveWorkers: clampInt(endpoint?.workersMin, 1, 1, 2),
  manualReserveHours: 1,
  manualWorkerStatus: "",
  lastUpdated: "",
  connected: false,
  loading: true,
  busyManualWorkers: false,
  busyAutoscaler: false,
  error: ""
});

const filters = [
  { id: "all", label: "All" },
  { id: "connected", label: "Online" },
  { id: "autoscaling", label: "Scheduled" },
  { id: "attention", label: "Needs attention" }
];

const formatNumber = (value) => new Intl.NumberFormat("en-US").format(value ?? 0);

const getGpuLabel = (endpoint = {}) => {
  const gpuIds = Array.isArray(endpoint.gpuIds) ? endpoint.gpuIds : [];
  if (!gpuIds.length) {
    return "Not reported";
  }
  return gpuIds.join(", ");
};

const getCostLabel = (endpoint = {}) => {
  const costValue = endpoint.costPerHr ?? endpoint.costPerHour ?? endpoint.gpuCost ?? endpoint.pricePerHour;
  if (costValue === undefined || costValue === null || costValue === "") {
    return "Not exposed by API";
  }

  const parsed = Number(costValue);
  return Number.isFinite(parsed) ? `$${parsed.toFixed(3)} / hr` : String(costValue);
};

const getScheduleClass = (status = "") => status.toLowerCase().replace(/[^a-z0-9]+/g, "-");

const getControlClass = (mode = "") => (mode === "Manual Reserve" ? "manual-override" : mode.toLowerCase().replace(/\s+/g, "-"));

const formatExpiration = (value) => {
  if (!value) {
    return "duration end";
  }

  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "duration end" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};

const cardVariants = {
  hidden: { opacity: 0, y: 18, scale: 0.98 },
  visible: (index) => ({
    opacity: 1,
    y: 0,
    scale: 1,
    transition: {
      delay: Math.min(index * 0.045, 0.28),
      duration: 0.36,
      ease: [0.22, 1, 0.36, 1]
    }
  }),
  exit: { opacity: 0, y: 12, scale: 0.98, transition: { duration: 0.18 } }
};

function AnimatedStatValue({ value }) {
  const [changed, setChanged] = useState(false);
  const previousValue = useRef(value);

  useEffect(() => {
    if (previousValue.current === value) {
      return undefined;
    }

    previousValue.current = value;
    setChanged(true);
    const timer = setTimeout(() => {
      setChanged(false);
    }, 420);

    return () => clearTimeout(timer);
  }, [value]);

  return <strong className={`stat-value ${changed ? "changed" : ""}`}>{value}</strong>;
}

export default function RunpodManagement() {
  const [access, setAccess] = useState(null);
  const [endpoints, setEndpoints] = useState([]);
  const [cardsById, setCardsById] = useState({});
  const [globalBusy, setGlobalBusy] = useState(false);
  const [lastRefresh, setLastRefresh] = useState("");
  const [syncFailed, setSyncFailed] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [query, setQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState("all");
  const [selectedEndpointId, setSelectedEndpointId] = useState(null);
  const [panelWidth, setPanelWidth] = useState(480);
  const [isResizingPanel, setIsResizingPanel] = useState(false);

  const syncLockRef = useRef(false);
  const canManageWorkers = Boolean(access?.permissions?.manageWorkers);
  const canManageSchedule = Boolean(access?.permissions?.manageSchedule);

  useEffect(() => {
    let isMounted = true;

    const loadAccess = async () => {
      try {
        const session = await fetchSession();
        if (isMounted) {
          setAccess(session);
        }
      } catch (error) {
        if (isMounted) {
          setAccess({
            role: "user",
            permissions: {
              manageWorkers: false,
              manageSchedule: false
            }
          });
          setSyncFailed(true);
        }
      }
    };

    loadAccess();

    return () => {
      isMounted = false;
    };
  }, []);

  const syncEndpointList = useCallback((endpointList) => {
    setEndpoints(endpointList);
    setCardsById((previous) => {
      const next = {};
      for (const endpoint of endpointList) {
        const existing = previous[endpoint.id];
        if (!existing) {
          next[endpoint.id] = createCardState(endpoint);
          continue;
        }

        next[endpoint.id] = {
          ...existing,
          endpoint: { ...(existing.endpoint ?? {}), ...endpoint }
        };

      }
      return next;
    });
  }, []);

  const refreshEndpoint = useCallback(async (endpointId, endpointHint = null) => {
    const [dashboardResult, automationResult, liveResult] = await Promise.allSettled([
      fetchDashboard(endpointId),
      fetchAutomation(endpointId),
      fetchLive(endpointId)
    ]);

    setCardsById((previous) => {
      const current = previous[endpointId] ?? createCardState(endpointHint ?? { id: endpointId, name: endpointId });
      const next = {
        ...current,
        loading: false,
        error: ""
      };

      if (dashboardResult.status === "fulfilled") {
        const dashboard = dashboardResult.value;
        next.endpoint = dashboard.endpoint ?? next.endpoint;
        next.health = dashboard.health ?? null;
        next.connected = true;
        next.lastUpdated = new Date().toLocaleTimeString();
      } else {
        next.connected = false;
        next.error = dashboardResult.reason?.message ?? "Failed to load endpoint health.";
      }

      if (automationResult.status === "fulfilled") {
        next.automation = normalizeAutomation(automationResult.value);
      } else if (!next.error) {
        next.error = automationResult.reason?.message ?? "Failed to load autoscaler settings.";
      }

      if (liveResult.status === "fulfilled") {
        next.live = {
          diagnostics: liveResult.value?.diagnostics ?? null,
          events: liveResult.value?.events ?? [],
          history: liveResult.value?.history ?? []
        };
      } else if (!next.error) {
        next.error = liveResult.reason?.message ?? "Failed to load live telemetry.";
      }

      return {
        ...previous,
        [endpointId]: next
      };
    });

    return dashboardResult.status === "fulfilled" && automationResult.status === "fulfilled" && liveResult.status === "fulfilled";
  }, []);

  const refreshAll = useCallback(
    async ({ silent = false } = {}) => {
      if (syncLockRef.current) {
        return;
      }

      syncLockRef.current = true;
      setIsSyncing(true);

      if (!silent) {
        setGlobalBusy(true);
      }

      try {
        const endpointList = await fetchEndpoints();
        syncEndpointList(endpointList);

        if (endpointList.length > 0) {
          const results = await Promise.all(endpointList.map((endpoint) => refreshEndpoint(endpoint.id, endpoint)));
          const hasFailure = results.some((result) => !result);
          setSyncFailed(hasFailure);
        } else {
          setSyncFailed(false);
        }

        setLastRefresh(new Date().toLocaleTimeString());
      } catch (error) {
        setSyncFailed(true);
      } finally {
        if (!silent) {
          setGlobalBusy(false);
        }
        setIsSyncing(false);
        syncLockRef.current = false;
      }
    },
    [refreshEndpoint, syncEndpointList]
  );

  useEffect(() => {
    if (canManageWorkers) {
      refreshAll();
    }
  }, [canManageWorkers, refreshAll]);

  useEffect(() => {
    if (!canManageWorkers) {
      return undefined;
    }

    const interval = setInterval(() => {
      refreshAll({ silent: true });
    }, 10_000);

    return () => clearInterval(interval);
  }, [canManageWorkers, refreshAll]);

  useEffect(() => {
    if (!isResizingPanel) {
      return undefined;
    }

    const handlePointerMove = (event) => {
      const nextWidth = window.innerWidth - event.clientX;
      setPanelWidth(Math.min(760, Math.max(360, nextWidth)));
    };

    const handlePointerUp = () => {
      setIsResizingPanel(false);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);

    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };
  }, [isResizingPanel]);

  const cards = useMemo(() => endpoints.map((endpoint) => cardsById[endpoint.id] ?? createCardState(endpoint)), [cardsById, endpoints]);

  const selectedCard = useMemo(() => {
    if (!selectedEndpointId) {
      return null;
    }
    return cardsById[selectedEndpointId] ?? null;
  }, [cardsById, selectedEndpointId]);

  const fleetStats = useMemo(() => {
    const totals = cards.reduce(
      (accumulator, card) => {
        const workers = card.health?.workers ?? {};
        const jobs = card.health?.jobs ?? {};
        accumulator.endpoints += 1;
        accumulator.online += card.connected ? 1 : 0;
        accumulator.running += workers.running ?? 0;
        accumulator.ready += workers.ready ?? 0;
        accumulator.idle += workers.idle ?? 0;
        accumulator.queue += jobs.inQueue ?? 0;
        accumulator.inProgress += jobs.inProgress ?? 0;
        accumulator.autoscaling += card.automation?.scheduleEnabled ? 1 : 0;
        accumulator.alerts += card.error ? 1 : 0;
        return accumulator;
      },
      { endpoints: 0, online: 0, running: 0, ready: 0, idle: 0, queue: 0, inProgress: 0, autoscaling: 0, alerts: 0 }
    );

    return {
      ...totals,
      onlineRatio: totals.endpoints ? Math.round((totals.online / totals.endpoints) * 100) : 0
    };
  }, [cards]);

  const visibleCards = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return cards.filter((card) => {
      const endpointName = card.endpoint?.name?.toLowerCase() ?? "";
      const endpointId = card.endpoint?.id?.toLowerCase() ?? "";
      const matchesQuery = !normalizedQuery || endpointName.includes(normalizedQuery) || endpointId.includes(normalizedQuery);
      const matchesFilter =
        activeFilter === "all" ||
        (activeFilter === "connected" && card.connected) ||
        (activeFilter === "autoscaling" && card.automation?.scheduleEnabled) ||
        (activeFilter === "attention" && (card.error || !card.connected));

      return matchesQuery && matchesFilter;
    });
  }, [activeFilter, cards, query]);

  const updateCardLocal = (endpointId, updater) => {
    setCardsById((previous) => {
      const current = previous[endpointId];
      if (!current) {
        return previous;
      }
      return {
        ...previous,
        [endpointId]: updater(current)
      };
    });
  };

  const handleToggleSchedule = (endpointId) => {
    if (!canManageSchedule) {
      return;
    }

    updateCardLocal(endpointId, (card) => ({
      ...card,
      automation: {
        ...card.automation,
        scheduleEnabled: !card.automation.scheduleEnabled,
        autoscalerEnabled: !card.automation.scheduleEnabled
      }
    }));
  };

  const handleScheduleField = (endpointId, field, value) => {
    if (!canManageSchedule) {
      return;
    }

    updateCardLocal(endpointId, (card) => ({
      ...card,
      automation: {
        ...card.automation,
        [field]: field === "activeWorkers" ? Math.max(0, toSafeInt(value, card.automation.activeWorkers)) : value
      }
    }));
  };

  const handleManualWorkerInput = (endpointId, value) => {
    if (!canManageWorkers) {
      return;
    }

    updateCardLocal(endpointId, (card) => ({
      ...card,
      manualReserveWorkers: clampInt(value, card.manualReserveWorkers, 1, 2),
      manualWorkerStatus: ""
    }));
  };

  const handleManualDurationInput = (endpointId, value) => {
    if (!canManageWorkers) {
      return;
    }

    updateCardLocal(endpointId, (card) => ({
      ...card,
      manualReserveHours: clampInt(value, card.manualReserveHours, 1, 2),
      manualWorkerStatus: ""
    }));
  };

  const handleApplyManualWorkers = async (endpointId) => {
    if (!canManageWorkers) {
      return;
    }

    const current = cardsById[endpointId];
    if (!current?.endpoint) {
      return;
    }

    const targetWorkers = clampInt(current.manualReserveWorkers, 1, 1, 2);
    const durationHours = clampInt(current.manualReserveHours, 1, 1, 2);

    const maxWorkers = Math.max(targetWorkers, toSafeInt(current.endpoint.workersMax, 0));
    const idleTimeout = Math.max(0, toSafeInt(current.endpoint.idleTimeout, 5));

    updateCardLocal(endpointId, (card) => ({ ...card, busyManualWorkers: true, manualWorkerStatus: "", error: "" }));

    try {
      await updateWorkers(endpointId, {
        activeWorkers: targetWorkers,
        maxWorkers,
        idleTimeout,
        manualOverride: true,
        durationHours
      });
      await refreshEndpoint(endpointId, current.endpoint);
      updateCardLocal(endpointId, (card) => ({
        ...card,
        manualReserveWorkers: targetWorkers,
        manualReserveHours: durationHours,
        manualWorkerStatus: `Reserved ${targetWorkers} worker${targetWorkers === 1 ? "" : "s"} for ${durationHours} hour${durationHours === 1 ? "" : "s"}.`
      }));
    } catch (error) {
      updateCardLocal(endpointId, (card) => ({
        ...card,
        manualWorkerStatus: `Manual update failed: ${error.message}`,
        connected: false
      }));
      setSyncFailed(true);
    } finally {
      updateCardLocal(endpointId, (card) => ({ ...card, busyManualWorkers: false }));
    }
  };

  const handleResumeSchedule = async (endpointId) => {
    if (!canManageWorkers) {
      return;
    }

    const current = cardsById[endpointId];
    if (!current) {
      return;
    }

    updateCardLocal(endpointId, (card) => ({ ...card, busyManualWorkers: true, manualWorkerStatus: "", error: "" }));

    try {
      const saved = await saveAutomation(endpointId, {
        manualOverride: {
          enabled: false,
          activeWorkers: 0
        }
      });
      updateCardLocal(endpointId, (card) => ({
        ...card,
        automation: normalizeAutomation(saved),
        manualWorkerStatus: "Schedule control resumed."
      }));
      await refreshEndpoint(endpointId, current.endpoint);
    } catch (error) {
      updateCardLocal(endpointId, (card) => ({
        ...card,
        manualWorkerStatus: `Could not resume schedule: ${error.message}`,
        connected: false
      }));
      setSyncFailed(true);
    } finally {
      updateCardLocal(endpointId, (card) => ({ ...card, busyManualWorkers: false }));
    }
  };

  const handleWorkingDayToggle = (endpointId, day) => {
    if (!canManageSchedule) {
      return;
    }

    if (day === 0 || day === 6) {
      return;
    }

    updateCardLocal(endpointId, (card) => {
      const selected = new Set(card.automation.workingDays);
      if (selected.has(day)) {
        selected.delete(day);
      } else {
        selected.add(day);
      }

      return {
        ...card,
        automation: {
          ...card.automation,
          workingDays: normalizeWorkingDays([...selected])
        }
      };
    });
  };

  const handleSaveSchedule = async (endpointId) => {
    if (!canManageSchedule) {
      return;
    }

    const current = cardsById[endpointId];
    if (!current) {
      return;
    }

    updateCardLocal(endpointId, (card) => ({ ...card, busyAutoscaler: true, error: "" }));

    try {
      const saved = await saveAutomation(endpointId, {
        scheduleEnabled: current.automation.scheduleEnabled,
        autoscalerEnabled: current.automation.scheduleEnabled,
        startTime: current.automation.startTime,
        endTime: current.automation.endTime,
        activeWorkers: current.automation.activeWorkers,
        startWorkers: current.automation.activeWorkers,
        workingDays: current.automation.workingDays,
        nightSafetyLockEnabled: current.automation.nightSafetyLockEnabled
      });
      updateCardLocal(endpointId, (card) => ({
        ...card,
        automation: normalizeAutomation(saved)
      }));
      await refreshEndpoint(endpointId, current.endpoint);
    } catch (error) {
      updateCardLocal(endpointId, (card) => ({ ...card, error: error.message, connected: false }));
      setSyncFailed(true);
    } finally {
      updateCardLocal(endpointId, (card) => ({ ...card, busyAutoscaler: false }));
    }
  };

  const handleNightSafetyToggle = (endpointId) => {
    if (!canManageSchedule) {
      return;
    }

    updateCardLocal(endpointId, (card) => ({
      ...card,
      automation: {
        ...card.automation,
        nightSafetyLockEnabled: !card.automation.nightSafetyLockEnabled
      }
    }));
  };

  if (access === null) {
    return (
      <div className="studio-shell">
        <section className="empty-state">
          <h2>Checking Access</h2>
          <p>Loading management permissions...</p>
        </section>
      </div>
    );
  }

  if (!canManageWorkers) {
    return (
      <div className="studio-shell">
        <section className="empty-state">
          <h2>Management Unavailable</h2>
          <p>Your account does not have worker management access.</p>
        </section>
      </div>
    );
  }

  return (
    <div className="studio-shell">
      <header className="studio-header">
        <div className="studio-title-wrap">
          <span className="eyebrow">RunPod Fleet Console</span>
          <h1>Management Studio</h1>
          <p className={`sync-line ${syncFailed ? "failed" : ""}`}>
            <span className={`sync-dot ${syncFailed ? "failed" : "active"} ${isSyncing && !syncFailed ? "spinning" : ""}`} />
            {syncFailed ? "Sync Failed" : lastRefresh ? `Last update ${lastRefresh}` : "Starting background sync..."}
          </p>
        </div>

        <div className="header-actions">
          <div className="search-wrap">
            <span>Search</span>
            <input
              type="search"
              placeholder="Endpoint name or id"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <button type="button" className="btn-ghost" onClick={() => refreshAll()} disabled={globalBusy || isSyncing}>
            {globalBusy || isSyncing ? "Refreshing..." : "Refresh Fleet"}
          </button>
        </div>
      </header>

      <section className="fleet-hero" aria-label="Fleet overview">
        <motion.div className="hero-panel hero-main" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }}>
          <div>
            <span className="metric-label">Online Health</span>
            <strong>{fleetStats.onlineRatio}%</strong>
            <p>
              {formatNumber(fleetStats.online)} of {formatNumber(fleetStats.endpoints)} endpoints responding
            </p>
          </div>
          <div className="orbit-ring" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
        </motion.div>

        <motion.div className="hero-panel" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }}>
          <span className="metric-label">Active Load</span>
          <strong>{formatNumber(fleetStats.running)}</strong>
          <p>{formatNumber(fleetStats.inProgress)} jobs in progress</p>
        </motion.div>

        <motion.div className="hero-panel" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}>
          <span className="metric-label">Queue</span>
          <strong>{formatNumber(fleetStats.queue)}</strong>
          <p>{formatNumber(fleetStats.ready + fleetStats.idle)} workers ready or idle</p>
        </motion.div>

        <motion.div className="hero-panel" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }}>
          <span className="metric-label">Schedules</span>
          <strong>{formatNumber(fleetStats.autoscaling)}</strong>
          <p>{fleetStats.alerts ? `${fleetStats.alerts} card${fleetStats.alerts === 1 ? "" : "s"} need attention` : "No visible alerts"}</p>
        </motion.div>
      </section>

      <nav className="filter-bar" aria-label="Fleet filters">
        {filters.map((filter) => (
          <button
            key={filter.id}
            type="button"
            className={`filter-chip ${activeFilter === filter.id ? "active" : ""}`}
            onClick={() => setActiveFilter(filter.id)}
          >
            {filter.label}
          </button>
        ))}
      </nav>

      {!globalBusy && endpoints.length === 0 ? (
        <section className="empty-state">
          <h2>No Active Servers</h2>
          <p>We could not find any active endpoints for this account.</p>
          <button type="button" className="btn-primary" onClick={() => refreshAll()}>
            Reload
          </button>
        </section>
      ) : (
        <section className="server-grid">
          <AnimatePresence>
          {visibleCards.map((card, index) => {
            const diagnostics = card.live?.diagnostics ?? null;
            const workers = card.health?.workers ?? {};
            const jobs = card.health?.jobs ?? {};
            const autoscalerLive = Boolean(diagnostics?.autoscalerActive);
            const scheduleStatus = diagnostics?.scheduleStatus ?? (card.automation.scheduleEnabled ? "Pending check" : "Inactive");
            const endpointActiveWorkers = card.endpoint?.workersMin ?? 0;
            const endpointId = card.endpoint?.id;
            const nightLockActive = Boolean(diagnostics?.nightSafetyLockActive);
            const isSelected = selectedEndpointId === endpointId;
            const scheduleEnabled = Boolean(card.automation?.scheduleEnabled);
            const manualOverrideActive = Boolean(diagnostics?.manualOverrideActive || card.automation.manualOverride?.enabled);
            const controlMode = nightLockActive ? "Night Safety Lock" : manualOverrideActive ? "Manual Reserve" : scheduleEnabled ? "Scheduler" : "Manual";
            const controlClass = getControlClass(controlMode);

            return (
              <motion.article
                key={endpointId}
                className={`server-card ${autoscalerLive ? "autoscaler-live" : ""} ${isSelected ? "selected" : ""} ${scheduleEnabled ? "schedule-enabled" : ""}`}
                variants={cardVariants}
                initial="hidden"
                animate="visible"
                exit="exit"
                custom={index}
                layout
              >
                <button type="button" className="card-summary" onClick={() => setSelectedEndpointId(endpointId)}>
                  <div className="card-header">
                    <div>
                      <h2>{card.endpoint?.name ?? endpointId ?? "Endpoint"}</h2>
                      <p className="endpoint-id">{endpointId}</p>
                    </div>
                    <span className={`status-pill ${card.connected ? "connected" : "disconnected"}`}>
                      <span className="pulse-dot" />
                      {card.connected ? "Connected" : "Disconnected"}
                    </span>
                  </div>

                  <div className="collapsed-metrics">
                    <div className="worker-orb">
                      <span>Reserve Workers</span>
                      <strong>{endpointActiveWorkers}</strong>
                    </div>
                    <div>
                      <span>Schedule</span>
                      <strong className={`schedule-text ${getScheduleClass(scheduleStatus)}`}>{scheduleStatus}</strong>
                    </div>
                    <div>
                      <span>GPU</span>
                      <strong>{getGpuLabel(card.endpoint)}</strong>
                    </div>
                    <div>
                      <span>Updated</span>
                      <strong>{card.lastUpdated || lastRefresh || "Waiting"}</strong>
                    </div>
                  </div>

                  <div className="card-worker-stats" aria-label="Worker status">
                    <span>
                      <strong>{workers.running ?? 0}</strong>
                      Running
                    </span>
                    <span>
                      <strong>{workers.idle ?? 0}</strong>
                      Idle
                    </span>
                    <span>
                      <strong>{jobs.inProgress ?? 0}</strong>
                      In Progress
                    </span>
                    <span>
                      <strong>{jobs.inQueue ?? 0}</strong>
                      Queue
                    </span>
                  </div>

                  <div className="summary-footer">
                    <span>{nightLockActive ? "Night Safety Lock: Active" : `Cost: ${getCostLabel(card.endpoint)}`}</span>
                    <span className="card-action-group">
                      <span className={`control-badge ${controlClass}`}>Control: {controlMode}</span>
                      {scheduleEnabled && <span className="scheduled-badge">Scheduled</span>}
                      <span className="expand-indicator">{isSelected ? "Selected" : "Settings"}</span>
                    </span>
                  </div>
                </button>

                {card.error && <p className="card-error">{card.error}</p>}
              </motion.article>
            );
          })}
          </AnimatePresence>
          {endpoints.length > 0 && visibleCards.length === 0 && (
            <section className="empty-state empty-filter">
              <h2>No Matching Servers</h2>
              <p>Try a different search term or switch the fleet filter back to All.</p>
              <button type="button" className="btn-primary" onClick={() => { setQuery(""); setActiveFilter("all"); }}>
                Clear filters
              </button>
            </section>
          )}
        </section>
      )}

      <AnimatePresence>
        {selectedCard && (
          <motion.aside
            className="config-panel"
            style={{ width: panelWidth }}
            initial={{ x: "100%", opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: "100%", opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
          >
            {(() => {
              const endpointId = selectedCard.endpoint?.id;
              const diagnostics = selectedCard.live?.diagnostics ?? null;
              const jobs = selectedCard.health?.jobs ?? {};
              const scheduleStatus = diagnostics?.scheduleStatus ?? (selectedCard.automation.scheduleEnabled ? "Pending check" : "Inactive");
              const activeWorkers = selectedCard.endpoint?.workersMin ?? 0;
              const nightLockActive = Boolean(diagnostics?.nightSafetyLockActive);
              const manualOverrideActive = Boolean(diagnostics?.manualOverrideActive || selectedCard.automation.manualOverride?.enabled);
              const latestEvent = selectedCard.live?.events?.[0] ?? "Waiting for automation event stream...";
              const controlMode = nightLockActive ? "Night Safety Lock" : manualOverrideActive ? "Manual Reserve" : selectedCard.automation.scheduleEnabled ? "Scheduler" : "Manual";

              return (
                <>
                  <button
                    type="button"
                    className="panel-resize-handle"
                    onPointerDown={(event) => {
                      event.preventDefault();
                      setIsResizingPanel(true);
                    }}
                    aria-label="Resize configuration panel"
                  />

                  <div className="panel-header">
                    <div>
                      <span className="eyebrow">Pod Settings</span>
                      <h2>{selectedCard.endpoint?.name ?? endpointId ?? "Endpoint"}</h2>
                      <p>{endpointId}</p>
                    </div>
                    <button type="button" className="panel-close" onClick={() => setSelectedEndpointId(null)} aria-label="Close settings panel">
                      Close
                    </button>
                  </div>

                  <div className="panel-status-row">
                    <span className={`status-pill ${selectedCard.connected ? "connected" : "disconnected"}`}>
                      <span className="pulse-dot" />
                      {selectedCard.connected ? "Connected" : "Disconnected"}
                    </span>
                    <span className={`panel-badge ${getScheduleClass(scheduleStatus)}`}>{scheduleStatus}</span>
                    <span className={`panel-badge control-${getControlClass(controlMode)}`}>
                      Control: {controlMode}
                    </span>
                  </div>

                  <div className="panel-hero">
                    <div className="worker-orb">
                      <span>Reserve Workers</span>
                      <strong>{activeWorkers}</strong>
                    </div>
                    <div>
                      <span>Last Updated</span>
                      <strong>{selectedCard.lastUpdated || lastRefresh || "Waiting"}</strong>
                      <p>{nightLockActive ? "Workers forced to 0 until 9:00 AM" : diagnostics?.scheduleReason ?? "Waiting for schedule sync"}</p>
                    </div>
                  </div>

                  <div className={`schedule-banner ${getScheduleClass(scheduleStatus)}`}>
                    <div>
                      <span>Schedule Status</span>
                      <strong>{scheduleStatus}</strong>
                      <p>{diagnostics?.scheduleReason ?? "Waiting for the next schedule check"}</p>
                    </div>
                    <span>{diagnostics?.currentDay ?? "Day"} · {diagnostics?.currentTime ?? "Syncing"}</span>
                  </div>

                  {selectedCard.error && <p className="card-error panel-error">{selectedCard.error}</p>}

                  <section className="panel-section manual-worker-section">
                    <div className="manual-worker-header">
                      <div>
                        <h3>Manual Reserve Workers</h3>
                        <p>
                          {manualOverrideActive
                            ? "Manual reservation is active until its duration ends or a safety rule resets it."
                            : selectedCard.automation.scheduleEnabled
                            ? "Scheduler resumes automatically when the manual reservation ends."
                            : "Without an active schedule, workers return to 0 when the manual reservation ends."}
                        </p>
                      </div>
                      <span className={`control-mode-pill ${getControlClass(controlMode)}`}>
                        {controlMode}
                      </span>
                    </div>

                    <div className="manual-worker-controls">
                      <label className="manual-slider-control">
                        <span>Manual Reserve Worker Count</span>
                        <strong>{selectedCard.manualReserveWorkers} worker{selectedCard.manualReserveWorkers === 1 ? "" : "s"}</strong>
                        <input
                          type="range"
                          min="1"
                          max="2"
                          step="1"
                          value={selectedCard.manualReserveWorkers}
                          onChange={(event) => handleManualWorkerInput(endpointId, event.target.value)}
                        />
                        <div className="slider-range-labels">
                          <span>1</span>
                          <span>2</span>
                        </div>
                      </label>
                      <label className="manual-slider-control">
                        <span>Manual Reserve Duration</span>
                        <strong>{selectedCard.manualReserveHours} hour{selectedCard.manualReserveHours === 1 ? "" : "s"}</strong>
                        <input
                          type="range"
                          min="1"
                          max="2"
                          step="1"
                          value={selectedCard.manualReserveHours}
                          onChange={(event) => handleManualDurationInput(endpointId, event.target.value)}
                        />
                        <div className="slider-range-labels">
                          <span>1h</span>
                          <span>2h</span>
                        </div>
                      </label>
                      <button
                        type="button"
                        className="btn-ghost"
                        onClick={() => handleApplyManualWorkers(endpointId)}
                        disabled={selectedCard.busyManualWorkers}
                      >
                        {selectedCard.busyManualWorkers ? "Applying..." : "Apply Manual Count"}
                      </button>
                    </div>

                    {manualOverrideActive && !nightLockActive && (
                      <div className="manual-reserve-active">
                        <span>
                          Active reserve ends at {formatExpiration(selectedCard.automation.manualOverride?.expiresAt)}
                        </span>
                        <button
                          type="button"
                          className="resume-schedule-button"
                          onClick={() => handleResumeSchedule(endpointId)}
                          disabled={selectedCard.busyManualWorkers}
                        >
                          Resume Schedule Control
                        </button>
                      </div>
                    )}

                    {selectedCard.manualWorkerStatus && (
                      <div className={`manual-worker-status ${/failed|could not/i.test(selectedCard.manualWorkerStatus) ? "error" : "success"}`}>
                        <span>{selectedCard.manualWorkerStatus}</span>
                        <button
                          type="button"
                          onClick={() => updateCardLocal(endpointId, (card) => ({ ...card, manualWorkerStatus: "" }))}
                        >
                          Clear
                        </button>
                      </div>
                    )}
                  </section>

                  {canManageSchedule && (
                    <section className="panel-section">
                      <div className="schedule-control-header">
                        <div>
                          <h3>Smart Worker Schedule</h3>
                          <p>Automatically activates workers during selected hours and returns to zero after hours.</p>
                        </div>
                        <div className="toggle-group">
                          <span>{selectedCard.automation.scheduleEnabled ? "Enabled" : "Disabled"}</span>
                          <button
                            type="button"
                            className={`toggle ${selectedCard.automation.scheduleEnabled ? "on" : ""}`}
                            onClick={() => handleToggleSchedule(endpointId)}
                            aria-pressed={selectedCard.automation.scheduleEnabled}
                            aria-label={`Toggle worker schedule for ${selectedCard.endpoint?.name ?? endpointId}`}
                            disabled={selectedCard.busyAutoscaler}
                          >
                            <span className="toggle-thumb" />
                          </button>
                        </div>
                      </div>

                      <div className="schedule-form-grid panel-form-grid">
                        <label>
                          <span>Start Time</span>
                          <input
                            type="time"
                            value={selectedCard.automation.startTime}
                            onChange={(event) => handleScheduleField(endpointId, "startTime", event.target.value)}
                          />
                        </label>
                        <label>
                          <span>End Time</span>
                          <input
                            type="time"
                            value={selectedCard.automation.endTime}
                            onChange={(event) => handleScheduleField(endpointId, "endTime", event.target.value)}
                          />
                        </label>
                        <label>
                          <span>Reserve Worker Count</span>
                          <input
                            type="number"
                            min="0"
                            value={selectedCard.automation.activeWorkers}
                            onChange={(event) => handleScheduleField(endpointId, "activeWorkers", event.target.value)}
                          />
                        </label>
                      </div>

                      <div className="working-days schedule-section">
                        <span>Active Days</span>
                        <div>
                          {weekdays.map((day) => (
                            <button
                              key={day.id}
                              type="button"
                              className={`day-chip ${selectedCard.automation.workingDays.includes(day.id) ? "active" : ""}`}
                              onClick={() => handleWorkingDayToggle(endpointId, day.id)}
                              disabled={day.disabled}
                              title={day.disabled ? `${day.label} is always disabled for cost safety` : day.label}
                            >
                              {day.short}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div className={`night-lock-card ${nightLockActive ? "active" : ""}`}>
                        <div>
                          <span>Night Safety Lock</span>
                          <strong>{nightLockActive ? "Active" : selectedCard.automation.nightSafetyLockEnabled ? "Enabled" : "Disabled"}</strong>
                          <p>
                            {nightLockActive
                              ? "Workers forced to 0 until 9:00 AM"
                              : "When enabled, workers are forced to 0 from 8:00 PM until 9:00 AM."}
                          </p>
                        </div>
                        <button
                          type="button"
                          className={`toggle ${selectedCard.automation.nightSafetyLockEnabled ? "on" : ""}`}
                          onClick={() => handleNightSafetyToggle(endpointId)}
                          aria-pressed={selectedCard.automation.nightSafetyLockEnabled}
                          aria-label={`Toggle night safety lock for ${selectedCard.endpoint?.name ?? endpointId}`}
                          disabled={selectedCard.busyAutoscaler}
                        >
                          <span className="toggle-thumb" />
                        </button>
                      </div>
                    </section>
                  )}

                  <div className="details-grid panel-details-grid">
                    <div>
                      <span>GPU Type</span>
                      <strong>{getGpuLabel(selectedCard.endpoint)}</strong>
                    </div>
                    <div>
                      <span>Max Workers</span>
                      <strong>{selectedCard.endpoint?.workersMax ?? 0}</strong>
                    </div>
                    <div>
                      <span>Queue</span>
                      <strong>{jobs.inQueue ?? 0}</strong>
                    </div>
                    <div>
                      <span>In Progress</span>
                      <strong>{jobs.inProgress ?? 0}</strong>
                    </div>
                  </div>

                  <div className="live-strip panel-live-strip">
                    <div className="live-chart" aria-label="Recent worker telemetry">
                      {selectedCard.live?.history?.length ? (
                        <ResponsiveContainer width="100%" height={120}>
                          <AreaChart data={selectedCard.live.history} margin={{ top: 8, right: 2, bottom: 0, left: 2 }}>
                            <defs>
                              <linearGradient id={`running-panel-${endpointId}`} x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#f59d3d" stopOpacity={0.34} />
                                <stop offset="95%" stopColor="#f59d3d" stopOpacity={0} />
                              </linearGradient>
                              <linearGradient id={`idle-panel-${endpointId}`} x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#36b37e" stopOpacity={0.24} />
                                <stop offset="95%" stopColor="#36b37e" stopOpacity={0} />
                              </linearGradient>
                            </defs>
                            <Tooltip contentStyle={{ borderRadius: 12, border: "none", boxShadow: "0 12px 30px rgba(20, 35, 60, 0.18)" }} />
                            <Area type="monotone" dataKey="running" stroke="#f59d3d" strokeWidth={2.4} fill={`url(#running-panel-${endpointId})`} />
                            <Area type="monotone" dataKey="idle" stroke="#36b37e" strokeWidth={2.2} fill={`url(#idle-panel-${endpointId})`} />
                          </AreaChart>
                        </ResponsiveContainer>
                      ) : (
                        <div className="chart-placeholder">Telemetry warming up</div>
                      )}
                    </div>
                    <p className="event-line">{latestEvent}</p>
                  </div>

                  {canManageSchedule && (
                    <div className="panel-actions">
                      <p>
                        Target workers: {diagnostics?.targetWorkers ?? "waiting"} ·{" "}
                        {diagnostics?.inWindow ? "inside working hours" : "outside working hours"}
                      </p>
                      <button
                        type="button"
                        className="btn-primary"
                        onClick={() => handleSaveSchedule(endpointId)}
                        disabled={selectedCard.busyAutoscaler}
                      >
                        {selectedCard.busyAutoscaler ? "Saving..." : "Save / Apply Settings"}
                      </button>
                    </div>
                  )}
                </>
              );
            })()}
          </motion.aside>
        )}
      </AnimatePresence>
    </div>
  );
}
