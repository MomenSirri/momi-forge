# Momi Forge clean code bundle

Files in this folder:
- `server_upscaler_with_flux_enhancement.py` — cleaned Gradio app with proper RunPod failure handling
- `runpod_api_class.py` — fixed RunPod client using GET for status and terminal-state aware polling
- `utils.py` — trimmed helper module with only the functions needed by the current app
- `requirements.txt` — minimal dependency list for this cleaned app path

Recommended root layout:

```text
Momi Forge/
├── .env
├── requirements.txt
├── runpod_api_class.py
├── server_upscaler_with_flux_enhancement.py
├── utils.py
└── api_workflow/
    └── Seedvr_flux_upscaler_03.json
```

Suggested environment variables:
- `RUNPOD_API_KEY`
- `RUNPOD_POD_ID_SEED`
- `RUNPOD_TARGET_ENV=SEED`
- `USER_DB_PATH=users.db`
- `MOMI_WORKFLOW_FILE=Seedvr_flux_upscaler_03.json`
- `SAVE_DEBUG_PROMPT_JSON=0`
