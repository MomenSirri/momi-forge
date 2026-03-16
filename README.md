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
