import cors from "cors";
import express from "express";
import helmet from "helmet";
import https from "https";
import morgan from "morgan";
import selfsigned from "selfsigned";
import { config } from "./config.js";
import { startAutomationLoop } from "./automationEngine.js";
import { requireAdmin } from "./middleware/requireAdmin.js";
import { runpodRoutes } from "./routes/runpodRoutes.js";

const app = express();

app.use(
  helmet({
    crossOriginResourcePolicy: false
  })
);
app.use(
  cors({
    origin: config.webOrigin,
    credentials: true
  })
);
app.use(express.json({ limit: "1mb" }));
app.use(morgan("dev"));

app.get("/api/health", (req, res) => {
  res.json({
    ok: true,
    https: true,
    apiKeyConfigured: Boolean(config.runpodApiKey),
    timezone: config.timezone,
    now: new Date().toISOString()
  });
});

app.use("/api/runpod", requireAdmin, runpodRoutes);
app.use("/api", requireAdmin, runpodRoutes);

app.use((error, req, res, next) => {
  const status = error.response?.status ?? 500;
  const upstreamMessage = error.response?.data?.message;
  const message = upstreamMessage ?? error.message ?? "Unexpected server error.";

  res.status(status).json({ error: message });
});

const cert = selfsigned.generate([{ name: "commonName", value: "localhost" }], {
  days: 365,
  keySize: 2048,
  algorithm: "sha256",
  extensions: [
    {
      name: "subjectAltName",
      altNames: [
        { type: 2, value: "localhost" },
        { type: 7, ip: "127.0.0.1" }
      ]
    }
  ]
});

if (config.runpodApiKey) {
  startAutomationLoop();
} else {
  console.warn("[Warning] RUNPOD_API_KEY is not configured. Endpoints API will return errors.");
}

https
  .createServer(
    {
      key: cert.private,
      cert: cert.cert
    },
    app
  )
  .listen(config.apiPort, "0.0.0.0", () => {
    console.log(`[HTTPS] Backend ready at https://localhost:${config.apiPort}`);
  });
