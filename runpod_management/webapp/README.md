# RunPod Management Studio (Node.js + React)

High-end responsive dashboard with:

- React frontend with custom CSS and motion
- Node.js backend API for RunPod controls
- Background automation loop (Plan A and Plan B)
- HTTPS in development for both frontend and backend

## Quick Start

From `D:\runpod_mangment\pythonProject`, run:

```bat
run_webapp.bat
```

The launcher installs dependencies and starts:

- Frontend: `https://localhost:5173`
- Backend: `https://localhost:8843`

## Environment

The backend reads `RUNPOD_API_KEY` from:

1. `webapp/backend/.env`
2. fallback: project root `.env`

You can copy `webapp/backend/.env.example` to `webapp/backend/.env` and edit values there if you want local overrides.

## API Endpoints (backend)

- `GET /api/health`
- `GET /api/endpoints`
- `GET /api/endpoint/:endpointId/dashboard`
- `POST /api/endpoint/:endpointId/workers`
- `GET /api/endpoint/:endpointId/automation`
- `POST /api/endpoint/:endpointId/automation`
- `GET /api/endpoint/:endpointId/live`

The same RunPod routes are also available under `/api/runpod/*` for mounting this dashboard inside a larger Admin Analytics app:

- `GET /api/runpod/endpoints`
- `GET /api/runpod/endpoint/:endpointId/dashboard`
- `POST /api/runpod/endpoint/:endpointId/workers`
- `GET /api/runpod/endpoint/:endpointId/automation`
- `POST /api/runpod/endpoint/:endpointId/automation`
- `GET /api/runpod/endpoint/:endpointId/live`

Set `RUNPOD_ADMIN_TOKEN` to require an `X-Admin-Token` header or `Authorization: Bearer <token>` for RunPod admin routes. Localhost requests are allowed without that token for local development.
