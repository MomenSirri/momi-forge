# Momi Forge

Momi Forge is a Gradio-based image enhancement and upscaling app that sends workflow jobs to a RunPod serverless worker.

It is built for a practical production flow:
- local Gradio UI
- ComfyUI workflow JSON
- RunPod serverless execution
- result polling with clear terminal-state handling
- side-by-side comparison of input and output

## What it does

Momi Forge lets you:
- upload an image in the web UI
- choose an upscale mode and engine mode
- send the workflow to RunPod as JSON
- monitor job progress from the UI
- handle failed or cancelled jobs cleanly instead of hanging forever
- preview the original and processed image side by side

## Current project layout

```text
Momi Forge/
├── .env
├── .gitignore
├── requirements.txt
├── runpod_api_class.py
├── server_upscaler_with_flux_enhancement.py
├── utils.py
├── users.db                 # created automatically at runtime
└── api_workflow/
    └── Seedvr_flux_upscaler_03.json
```

## Core files

### `server_upscaler_with_flux_enhancement.py`
Main Gradio application.

Responsibilities:
- loads the workflow JSON
- prepares the payload
- submits jobs to RunPod
- polls job status
- handles `COMPLETED`, `FAILED`, `ERROR`, `TIMED_OUT`, and `CANCELLED`
- decodes the returned image
- displays before/after output in the UI

### `runpod_api_class.py`
Async-friendly RunPod client wrapper.

Responsibilities:
- reads RunPod credentials from environment variables
- sends `/run` requests
- checks `/status/{job_id}`
- requests cancellation
- returns useful failure messages back to the app

### `utils.py`
Small helper module.

Responsibilities:
- convert images to PIL
- encode input images to base64
- prepare RunPod payload JSON

### `api_workflow/Seedvr_flux_upscaler_03.json`
ComfyUI workflow exported in API format.

This is the workflow definition the Gradio app edits and submits to RunPod.

## Requirements

Install dependencies with:

```bash
pip install -r requirements.txt
```

Recommended setup on Windows PowerShell:

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Environment variables

Create a `.env` file in the project root.

Example:

```env
RUNPOD_API_KEY=your_runpod_api_key
RUNPOD_POD_ID_SEED=your_serverless_endpoint_id
RUNPOD_TARGET_ENV=SEED
USER_DB_PATH=users.db
MOMI_WORKFLOW_FILE=Seedvr_flux_upscaler_03.json
SAVE_DEBUG_PROMPT_JSON=0
```

### Notes

- `RUNPOD_TARGET_ENV=SEED` means the app will use `RUNPOD_POD_ID_SEED`.
- The RunPod ID here should be your **serverless endpoint ID**.
- `users.db` is created automatically if it does not exist.

## User management (company sign-in)

The app now supports internal BrickVisual sign-in and only accepts `@brickvisual.com` users.

User records are stored in `users.db` table `users` with:
- `email`
- `pwd_hash` (bcrypt)
- `role` (`user` or `admin`)
- `is_active` (1/0)

Use the built-in admin CLI:

```powershell
python manage_users.py --help
```

### 1) Add (or update) a user + set password

```powershell
python manage_users.py upsert --email john.smith@brickvisual.com --role user
```

It will prompt securely for password and confirmation.

### 2) Set/reset password

```powershell
python manage_users.py set-password --email john.smith@brickvisual.com
```

### 3) Grant/remove admin role

```powershell
python manage_users.py set-role --email john.smith@brickvisual.com --role admin
python manage_users.py set-role --email john.smith@brickvisual.com --role user
```

### 4) Activate/deactivate account

```powershell
python manage_users.py activate --email john.smith@brickvisual.com
python manage_users.py deactivate --email john.smith@brickvisual.com
```

### 5) Inspect users

```powershell
python manage_users.py show --email john.smith@brickvisual.com
python manage_users.py list
python manage_users.py list --active-only
python manage_users.py list --role admin
```

### 6) Avatar mapping

For profile pictures, place files in:

```text
bricker_image/<email_prefix>.png
```

Example:

```text
john.smith@brickvisual.com -> bricker_image/john.smith.png

## One-click startup (Windows)

Use:

```bat
start_momi_forge.bat
```

This launches:
- History Portal (`history_portal`, Node.js) on `http://localhost:8199`
- Main Gradio app on `http://127.0.0.1:8170`

The launcher also sets:
- `USER_DB_PATH`
- `HISTORY_PORTAL_URL`
- `HISTORY_PORTAL_SSO_SECRET`

so the History iframe can auto sign-in from your existing Gradio session.

If you run manually (without the `.bat`), set the same `HISTORY_PORTAL_SSO_SECRET` value for both processes, otherwise the history portal will ask for a second login.
```

If not found, the app falls back to `bricker_image/default_avatar.png`.

### Optional: use a specific DB path

```powershell
python manage_users.py upsert --db "D:\Momi Forge\users.db" --email john.smith@brickvisual.com --role user
```

You can also set this globally in `.env`:

```env
USER_DB_PATH=D:\Momi Forge\users.db
APP_ADMIN_EMAILS=first.admin@brickvisual.com,second.admin@brickvisual.com
```

## Running the app

Start the app with:

```bash
python server_upscaler_with_flux_enhancement.py
```

Default local URL:

```text
http://0.0.0.0:8188
```

If that port is busy, change the port in the `launch()` block at the end of the file.

## History portal (custom web UI)

History is now served by a dedicated web app under:

```text
history_portal/
├── package.json
├── server.js
└── public/
    ├── index.html
    ├── styles.css
    └── app.js
```

This replaces Gradio widgets for the History surface.  
Gradio now only embeds the portal as a shell tab.

Architecture details: `HISTORY_PORTAL_ARCHITECTURE.md`

### Start the History portal

```powershell
cd "D:\Momi Forge\history_portal"
npm install
npm start
```

Or use the helper script from the project root:

```powershell
.\start_history_portal.ps1
```

Port conflict handling is built in:

```powershell
# Default: auto-pick next free port if 8199 is busy
.\start_history_portal.ps1

# Force-kill existing listener on requested port, then reuse it
.\start_history_portal.ps1 -Port 8199 -PortConflict Kill

# Fail immediately if requested port is busy
.\start_history_portal.ps1 -Port 8199 -PortConflict Fail
```

Default URL:

```text
http://127.0.0.1:8199
```

### History portal environment variables

Optional:

```env
HISTORY_PORTAL_HOST=127.0.0.1
HISTORY_PORTAL_PORT=8199
HISTORY_SESSION_TTL_MS=43200000
HISTORY_COOKIE_SECURE=0
```

The portal also reads:

```env
USER_DB_PATH
COMPANY_EMAIL_DOMAIN
BRICKER_IMAGE_DIR
TASK_THUMBNAIL_DIR
DEFAULT_AVATAR_FILENAME
```

### Connect Gradio to the portal

In `.env`:

```env
HISTORY_PORTAL_URL=http://127.0.0.1:8199
```

### API endpoints (History portal)

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `GET /api/history`
- `GET /api/history/:taskId`
- `POST /api/history/:taskId/favorite`
- `GET /api/favorite-categories`
- `POST /api/favorite-categories`
- `GET /api/asset?path=...`

## Error handling

One of the main improvements in this version is failure handling.

The app now stops polling and reports a message back to the UI when a RunPod job reaches any terminal failure state such as:
- `FAILED`
- `ERROR`
- `TIMED_OUT`
- `CANCELLED`

It also surfaces worker-side structured errors from the RunPod output payload when present.

## Git setup

Before pushing to GitHub, make sure these are ignored:
- `.env`
- `.venv`
- `users.db`
- logs
- temporary outputs

Then initialize and push normally:

```bash
git init
git add .
git commit -m "Initial commit: Momi Forge"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/momi-forge.git
git push -u origin main
```

## Recommended next cleanup

Your current layout is stable and should stay as-is until you finish validating the app.

After that, the safest professional cleanup would be:

```text
Momi Forge/
├── .env
├── .gitignore
├── README.md
├── requirements.txt
├── app.py
├── runpod_client.py
├── utils.py
└── workflows/
    └── seedvr_flux_upscaler.workflow.json
```

Suggested rename map:
- `server_upscaler_with_flux_enhancement.py` → `app.py`
- `runpod_api_class.py` → `runpod_client.py`
- `api_workflow/Seedvr_flux_upscaler_03.json` → `workflows/seedvr_flux_upscaler.workflow.json`

Do that only after the current version is fully tested so you do not break imports while things are finally working.

## Status

Current state:
- app launches correctly
- RunPod credentials load from `.env`
- RunPod jobs submit correctly
- failure states are handled cleanly
- project is ready to live in GitHub as a cleaner baseline
