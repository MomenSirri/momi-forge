# Momi Forge repo structure and naming plan

## Keep now

This is the safe, working structure right now:

```text
Momi Forge/
├── .env
├── .gitignore
├── README.md
├── requirements.txt
├── runpod_api_class.py
├── server_upscaler_with_flux_enhancement.py
├── utils.py
└── api_workflow/
    └── Seedvr_flux_upscaler_03.json
```

Use this while you are still validating the app.

## Recommended professional rename later

When you want a cleaner public repo, move to this:

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

## Rename map

- `server_upscaler_with_flux_enhancement.py` → `app.py`
- `runpod_api_class.py` → `runpod_client.py`
- `api_workflow/Seedvr_flux_upscaler_03.json` → `workflows/seedvr_flux_upscaler.workflow.json`

## Why this naming is better

- `app.py` is shorter and cleaner for the main entry point
- `runpod_client.py` says exactly what the file does
- `workflows/` is clearer than `api_workflow/`
- `.workflow.json` makes it obvious this JSON belongs to a workflow definition

## Suggested public repo description

Use one of these for GitHub:

### Option 1
A Gradio app for image enhancement and upscaling with RunPod serverless execution and ComfyUI workflow integration.

### Option 2
Professional image enhancement workflow UI built with Gradio, RunPod, and ComfyUI.

### Option 3
Serverless image enhancement and upscaling app with Gradio frontend and RunPod backend.
