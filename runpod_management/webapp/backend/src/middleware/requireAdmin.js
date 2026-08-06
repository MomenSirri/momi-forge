const localAddresses = new Set(["127.0.0.1", "::1", "::ffff:127.0.0.1"]);

const getBearerToken = (authorization = "") => {
  const [scheme, token] = authorization.split(" ");
  return scheme?.toLowerCase() === "bearer" ? token : null;
};

export const normalizeRole = (role = "") => {
  const normalized = String(role ?? "").trim().toLowerCase();
  return ["user", "admin", "ex"].includes(normalized) ? normalized : "user";
};

export const getRequestRole = (req) => normalizeRole(req.userRole ?? req.get("x-user-role") ?? req.get("x-admin-role"));

export const canManageWorkers = (role) => ["admin", "ex"].includes(normalizeRole(role));

export const canManageSchedule = (role) => normalizeRole(role) === "ex";

export const requireAdmin = (req, res, next) => {
  const configuredToken = process.env.RUNPOD_ADMIN_TOKEN;
  const role = getRequestRole(req);

  if (canManageWorkers(role)) {
    req.userRole = role;
    next();
    return;
  }

  if (configuredToken) {
    const suppliedToken = req.get("x-admin-token") ?? getBearerToken(req.get("authorization"));
    if (suppliedToken === configuredToken) {
      req.userRole = "ex";
      next();
      return;
    }

    res.status(403).json({ error: "Admin access is required." });
    return;
  }

  if (localAddresses.has(req.ip) || localAddresses.has(req.socket?.remoteAddress)) {
    req.userRole = "ex";
    next();
    return;
  }

  res.status(403).json({ error: "Admin access is required." });
};
