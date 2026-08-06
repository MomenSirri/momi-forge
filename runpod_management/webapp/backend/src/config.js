import dotenv from "dotenv";
import path from "path";

dotenv.config({ path: path.resolve(process.cwd(), ".env") });

if (!process.env.RUNPOD_API_KEY) {
  dotenv.config({ path: path.resolve(process.cwd(), "..", "..", ".env") });
}

if (!process.env.RUNPOD_API_KEY) {
  dotenv.config({ path: path.resolve(process.cwd(), "..", "..", "..", ".env") });
}

const toNumber = (value, fallback) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

export const config = {
  runpodApiKey: process.env.RUNPOD_API_KEY ?? "",
  apiPort: toNumber(process.env.WEBAPP_API_PORT, 8843),
  webOrigin: process.env.WEBAPP_ORIGIN ?? "https://localhost:5173",
  timezone: process.env.WEBAPP_TIMEZONE ?? "Europe/Budapest",
  pollIntervalMs: toNumber(process.env.WEBAPP_POLL_INTERVAL_MS, 30_000)
};
