import gradio as gr
from datetime import datetime
import time
import threading
import pandas as pd
import pytz
import runpod_client
from config import DASHBOARD_USERNAME, DASHBOARD_PASSWORD

# ==========================================
# 🧠 GLOBAL STATE & BACKGROUND SERVER MEMORY
# ==========================================
STATE_LOCK = threading.Lock()

AUTOMATION_STATE = {}
DIAGNOSTICS = {}
EVENT_HISTORY = {}
HISTORY = {}
MAX_HISTORY_POINTS = 120


def get_default_automation_state():
    return {
        "plan_a_enable": False, "plan_a_time": "17:00", "plan_a_workers": 4,
        "plan_a_end_time": "19:00", "plan_a_end_workers": 1,
        "plan_b_enable": False, "plan_b_thresh": 30, "plan_b_boost": 33,
        "last_applied_state": "none"  # Tracks if we've already scaled up or down today
    }


def format_time():
    return datetime.now().strftime("%H:%M:%S")


def get_budapest_time():
    tz = pytz.timezone('Europe/Budapest')
    return datetime.now(tz)


def add_log(ep_id, message):
    """Safely adds a timestamped log to the rolling history."""
    now_str = format_time()
    full_msg = f"[{now_str}] {message}"
    print(f"[Pod {ep_id}] {full_msg}")  # Print to terminal for easy debugging

    with STATE_LOCK:
        if ep_id not in EVENT_HISTORY:
            EVENT_HISTORY[ep_id] = []
        EVENT_HISTORY[ep_id].insert(0, full_msg)
        EVENT_HISTORY[ep_id] = EVENT_HISTORY[ep_id][:15]  # Keep last 15 logs


# ==========================================
# ⚙️ BACKGROUND THREAD (Runs 24/7)
# ==========================================
def background_automation_loop():
    print("🚀 Background Automation Server Started.")

    while True:
        try:
            eps, err = runpod_client.get_endpoints()
            if err or not eps:
                time.sleep(30)
                continue

            bp_time = get_budapest_time()
            current_hour_min = bp_time.strftime("%H:%M")
            now_str = format_time()

            for ep_data in eps:
                ep_id = ep_data['id']

                with STATE_LOCK:
                    if ep_id not in AUTOMATION_STATE:
                        AUTOMATION_STATE[ep_id] = get_default_automation_state()
                    config = AUTOMATION_STATE[ep_id].copy()

                # Fetch live health
                health_data, health_err = runpod_client.get_endpoint_health(ep_id)
                if health_err:
                    add_log(ep_id, f"❌ API Error fetching health: {health_err}")
                    continue

                workers = health_data.get("workers", {})
                idle = workers.get('idle', 0)
                running = workers.get('running', 0)
                init = workers.get('initializing', 0)
                total_active = idle + running + init

                # Update Graph Data
                new_rows = [
                    {"Time": now_str, "Status": "Idle", "Count": idle},
                    {"Time": now_str, "Status": "Running", "Count": running},
                    {"Time": now_str, "Status": "Initializing", "Count": init}
                ]
                with STATE_LOCK:
                    if ep_id not in HISTORY: HISTORY[ep_id] = pd.DataFrame(columns=["Time", "Status", "Count"])
                    df = pd.concat([HISTORY[ep_id], pd.DataFrame(new_rows)], ignore_index=True)
                    if len(df) > (MAX_HISTORY_POINTS * 3): df = df.iloc[-(MAX_HISTORY_POINTS * 3):]
                    HISTORY[ep_id] = df

                # --- 🕒 PLAN A TIME LOGIC ---
                in_rush_hour = False
                if config["plan_a_enable"]:
                    start_t = config["plan_a_time"]
                    end_t = config["plan_a_end_time"]
                    if start_t <= end_t:
                        in_rush_hour = (start_t <= current_hour_min < end_t)
                    else:
                        in_rush_hour = (current_hour_min >= start_t or current_hour_min < end_t)

                # Update Live Diagnostics Panel
                diag_md = f"""
                **Live Server Diagnostics**
                * 🕰️ **Budapest Clock:** `{current_hour_min}` (Last Check: `{now_str}`)
                * 📊 **Target Pod:** `{ep_data.get('name')}`
                * 🚦 **Plan A (Scheduled):** `{'🟢 Enabled' if config['plan_a_enable'] else '🔴 Disabled'}`
                * 🚦 **Plan B (Reactive):** `{'🟢 Enabled' if config['plan_b_enable'] else '🔴 Disabled'}`
                * 🕒 **Time Status:** `{'🔥 INSIDE Rush Hour' if in_rush_hour else '💤 Outside Rush Hour'}`
                * ⚙️ **Current State Flag:** `{config.get('last_applied_state', 'none')}`
                """
                with STATE_LOCK:
                    DIAGNOSTICS[ep_id] = diag_md

                # --- 🚀 AUTOMATION EXECUTION ---
                if config["plan_a_enable"]:
                    # If we are INSIDE the rush hour window, and we haven't boosted yet
                    if in_rush_hour and config.get("last_applied_state") != "start":
                        target = int(config["plan_a_workers"])
                        res, err = runpod_client.update_endpoint_config(ep_data, target,
                                                                        max(target, ep_data.get("workersMax", 0)))
                        if not err:
                            add_log(ep_id, f"⏰ PLAN A: Started Rush Hour. Scaled UP to {target} Active Workers.")
                            with STATE_LOCK:
                                AUTOMATION_STATE[ep_id]["last_applied_state"] = "start"
                        else:
                            add_log(ep_id, f"❌ PLAN A Start Error: {err}")

                    # If we are OUTSIDE the rush hour window, and we haven't scaled down yet
                    elif not in_rush_hour and config.get("last_applied_state") == "start":
                        target = int(config["plan_a_end_workers"])
                        res, err = runpod_client.update_endpoint_config(ep_data, target,
                                                                        max(target, ep_data.get("workersMax", 0)))
                        if not err:
                            add_log(ep_id, f"🛑 PLAN A: Ended Rush Hour. Scaled DOWN to {target} Active Workers.")
                            with STATE_LOCK:
                                AUTOMATION_STATE[ep_id]["last_applied_state"] = "end"
                        else:
                            add_log(ep_id, f"❌ PLAN A End Error: {err}")

                # --- 📉 PLAN B EXECUTION ---
                if config["plan_b_enable"] and total_active > 0:
                    avail_pct = (idle / total_active) * 100

                    if avail_pct <= config["plan_b_thresh"]:
                        boost = max(1, int(total_active * (config["plan_b_boost"] / 100.0)))
                        new_active = ep_data.get("workersMin", 0) + boost
                        new_max = max(ep_data.get("workersMax", 0), new_active)

                        res, err = runpod_client.update_endpoint_config(ep_data, new_active, new_max)
                        if not err: add_log(ep_id,
                                            f"📉 PLAN B: Critical capacity ({avail_pct:.1f}%). Added {boost} workers.")

                    elif avail_pct >= (config["plan_b_thresh"] + 40) and not in_rush_hour:
                        if ep_data.get("workersMin", 0) > 0:
                            new_active = ep_data.get("workersMin", 0) - 1
                            res, err = runpod_client.update_endpoint_config(ep_data, new_active,
                                                                            ep_data.get("workersMax", 0))
                            if not err: add_log(ep_id,
                                                f"♻️ PLAN B: High capacity ({avail_pct:.1f}%). Scaled down 1 worker.")

        except Exception as e:
            print(f"Background Loop Exception: {e}")

        time.sleep(30)


threading.Thread(target=background_automation_loop, daemon=True).start()


# ==========================================
# 🖥️ GRADIO UI FUNCTIONS
# ==========================================
def load_endpoints():
    eps, err = runpod_client.get_endpoints()
    if err: return gr.update(choices=[], value=None), [], f"❌ Error loading endpoints: {err}"
    if not eps: return gr.update(choices=[], value=None), [], "⚠️ No endpoints found."
    choices = [f"{ep['name']} ({ep['id']})" for ep in eps]
    return gr.update(choices=choices, value=choices[0]), eps, f"✅ Connected to RunPod at {format_time()}."


def refresh_ui_from_memory(selected_choice):
    """Extremely fast sync from memory to the UI components."""
    if not selected_choice:
        return gr.update(), "Awaiting selection...", "No events."

    ep_id = selected_choice.split("(")[-1].strip(")")

    with STATE_LOCK:
        df = HISTORY.get(ep_id, pd.DataFrame(columns=["Time", "Status", "Count"]))
        diag = DIAGNOSTICS.get(ep_id, "Initializing background sync...")
        events = "\n".join(EVENT_HISTORY.get(ep_id, ["Waiting for background loop..."]))

    if df.empty: return gr.update(), diag, events
    return gr.LinePlot(df, x="Time", y="Count", color="Status", title="Live Worker Status"), diag, events


def load_automation_settings_for_pod(selected_choice):
    if not selected_choice: return False, "17:00", 4, "19:00", 1, False, 30, 33
    ep_id = selected_choice.split("(")[-1].strip(")")
    with STATE_LOCK: config = AUTOMATION_STATE.get(ep_id, get_default_automation_state())
    return (config["plan_a_enable"], config["plan_a_time"], config["plan_a_workers"],
            config["plan_a_end_time"], config["plan_a_end_workers"], config["plan_b_enable"], config["plan_b_thresh"],
            config["plan_b_boost"])


def save_automation_settings_for_pod(selected_choice, a_en, a_t, a_w, a_e_t, a_e_w, b_en, b_th, b_bo):
    if not selected_choice: return "⚠️ Please select a pod first."
    ep_id = selected_choice.split("(")[-1].strip(")")

    with STATE_LOCK:
        AUTOMATION_STATE[ep_id] = {
            "plan_a_enable": a_en, "plan_a_time": a_t, "plan_a_workers": a_w,
            "plan_a_end_time": a_e_t, "plan_a_end_workers": a_e_w,
            "plan_b_enable": b_en, "plan_b_thresh": b_th, "plan_b_boost": b_bo,
            "last_applied_state": "none"  # Reset tracking so changes apply immediately!
        }
    add_log(ep_id, "💾 Saved new automation config. Background server updated.")
    return "✅ Rules saved."


def refresh_dashboard(selected_choice, endpoints_state):
    if not selected_choice or not endpoints_state: return "⚠️ No endpoint selected.", "No data", "No data", "No data", 0, 0, 5, {}
    ep_id = selected_choice.split("(")[-1].strip(")")
    ep_data = next((ep for ep in endpoints_state if ep["id"] == ep_id), None)
    if not ep_data: return "❌ Endpoint data not found.", "No data", "No data", "No data", 0, 0, 5, {}

    health_data, health_err = runpod_client.get_endpoint_health(ep_id)
    if health_err: return f"Failed at {format_time()}", f"**Error:** {health_err}", "", "", 0, 0, 5, {}

    jobs = health_data.get("jobs", {})
    workers = health_data.get("workers", {})

    health_md = f"**Workers:** 🟢 Ready: {workers.get('ready', 0)} | 🏃 Running: {workers.get('running', 0)} | 💤 Idle: {workers.get('idle', 0)} | ⏳ Init: {workers.get('initializing', 0)}\n**Jobs:** 📥 Queue: {jobs.get('inQueue', 0)} | ⚙️ Progress: {jobs.get('inProgress', 0)} | ✅ Done: {jobs.get('completed', 0)}"
    config_md = f"**ID:** `{ep_data.get('id')}` | **Template:** `{ep_data.get('templateId')}` | **Idle Timeout:** `{ep_data.get('idleTimeout', 5)}s`"

    return (f"✅ Refreshed at {format_time()}.", health_md, config_md, "✅ Refreshed.",
            ep_data.get("workersMin", 0), ep_data.get("workersMax", 0), ep_data.get("idleTimeout", 5), ep_data)


def handle_update_workers(ep_data, new_active, new_max, new_idle):
    if not ep_data: return "⚠️ Please load an endpoint first.", ep_data, 0, 0, 5
    if new_active > new_max: return "❌ Error: Active Workers cannot be greater than Max Workers.", ep_data, ep_data.get(
        "workersMin", 0), ep_data.get("workersMax", 0), ep_data.get("idleTimeout", 5)

    res, err = runpod_client.update_endpoint_config(ep_data, new_active, new_max, new_idle)
    if err: return f"❌ Error: {err}", ep_data, ep_data.get("workersMin", 0), ep_data.get("workersMax", 0), ep_data.get(
        "idleTimeout", 5)

    ep_data.update({"workersMin": res.get("workersMin", new_active), "workersMax": res.get("workersMax", new_max),
                    "idleTimeout": res.get("idleTimeout", new_idle)})
    return f"✅ Updated! ({format_time()})", ep_data, ep_data["workersMin"], ep_data["workersMax"], ep_data[
        "idleTimeout"]


# ==========================================
# 🎨 UI LAYOUT
# ==========================================
def create_ui():
    with gr.Blocks(title="RunPod Serverless Admin") as demo:
        endpoints_list_state = gr.State([])
        current_ep_state = gr.State({})

        gr.Markdown("# 🚀 RunPod Serverless Management Dashboard")
        status_bar = gr.Textbox(label="System Status", interactive=False, value="Connecting to RunPod API...")

        with gr.Row():
            with gr.Column(scale=1):
                endpoint_dropdown = gr.Dropdown(label="Select Endpoint", choices=[], interactive=True)
                btn_refresh = gr.Button("🔄 Force Manual Refresh", variant="primary")
            with gr.Column(scale=2):
                health_panel = gr.Markdown("Loading health data...")
                config_panel = gr.Markdown("Loading configuration...")

        with gr.Tabs():
            with gr.TabItem("⚙️ Manual Controls"):
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### ⚖️ Worker Settings")
                        active_input = gr.Number(label="Active Workers", precision=0)
                        max_input = gr.Number(label="Max Workers", precision=0)
                        idle_input = gr.Number(label="Idle Timeout (s)", precision=0)
                        btn_save = gr.Button("💾 Apply Settings", variant="primary")
                    with gr.Column():
                        gr.Markdown("### ⚡ Quick Actions")
                        btn_pause = gr.Button("⏸️ Emergency Pause (To 0)", variant="stop")

            with gr.TabItem("🤖 Smart Autoscaler (Background Server)"):
                with gr.Row():
                    with gr.Column(scale=2):
                        gr.Markdown("### 📈 Live Pod Status Graph")
                        live_graph = gr.LinePlot(x="Time", y="Count", color="Status", height=300,
                                                 tooltip=["Time", "Count", "Status"])

                        # NEW: Much better logging and diagnostics
                        with gr.Row():
                            diagnostic_panel = gr.Markdown("*Waiting for background server to sync...*")
                        automation_logs = gr.TextArea(label="Rolling Event History", interactive=False, lines=6)
                        btn_save_automation = gr.Button("💾 Save Automation Rules for this Pod", variant="primary",
                                                        size="lg")

                    with gr.Column(scale=1):
                        gr.Markdown("### 🕒 Plan A: Scheduled Rush Hour")
                        plan_a_enable = gr.Checkbox(label="Enable Scheduled Boost", value=False)

                        with gr.Row():
                            with gr.Column():
                                plan_a_time = gr.Textbox(label="Start Time", value="17:00", info="Budapest (HH:MM)")
                                plan_a_workers = gr.Number(label="Start Active Workers", value=4, precision=0)
                            with gr.Column():
                                plan_a_end_time = gr.Textbox(label="End Time", value="19:00", info="Budapest (HH:MM)")
                                plan_a_end_workers = gr.Number(label="End Active Workers", value=1, precision=0)

                        gr.Markdown("---")
                        gr.Markdown("### ⚖️ Plan B: Reactive Capacity")
                        plan_b_enable = gr.Checkbox(label="Enable Capacity Watchdog", value=False)
                        plan_b_thresh = gr.Slider(label="Critical Idle Threshold (%)", minimum=5, maximum=50, value=30,
                                                  step=5)
                        plan_b_boost = gr.Slider(label="Boost Amount (%)", minimum=10, maximum=100, value=33, step=1)

                ui_timer = gr.Timer(value=3, active=True)

        # WIRINGS
        demo.load(fn=load_endpoints, inputs=[], outputs=[endpoint_dropdown, endpoints_list_state, status_bar])

        endpoint_dropdown.change(
            fn=refresh_dashboard, inputs=[endpoint_dropdown, endpoints_list_state],
            outputs=[status_bar, health_panel, config_panel, gr.Markdown(), active_input, max_input, idle_input,
                     current_ep_state]
        ).then(
            fn=load_automation_settings_for_pod, inputs=[endpoint_dropdown],
            outputs=[plan_a_enable, plan_a_time, plan_a_workers, plan_a_end_time, plan_a_end_workers, plan_b_enable,
                     plan_b_thresh, plan_b_boost]
        )

        btn_refresh.click(fn=refresh_dashboard, inputs=[endpoint_dropdown, endpoints_list_state],
                          outputs=[status_bar, health_panel, config_panel, gr.Markdown(), active_input, max_input,
                                   idle_input, current_ep_state])
        btn_save.click(fn=handle_update_workers, inputs=[current_ep_state, active_input, max_input, idle_input],
                       outputs=[status_bar, current_ep_state, active_input, max_input, idle_input])
        btn_pause.click(fn=lambda ep: handle_update_workers(ep, 0, 0, ep.get("idleTimeout", 5)),
                        inputs=[current_ep_state],
                        outputs=[status_bar, current_ep_state, active_input, max_input, idle_input])

        btn_save_automation.click(
            fn=save_automation_settings_for_pod,
            inputs=[endpoint_dropdown, plan_a_enable, plan_a_time, plan_a_workers, plan_a_end_time, plan_a_end_workers,
                    plan_b_enable, plan_b_thresh, plan_b_boost],
            outputs=[status_bar]
        )

        ui_timer.tick(
            fn=refresh_ui_from_memory, inputs=[endpoint_dropdown],
            outputs=[live_graph, diagnostic_panel, automation_logs]
        )

    return demo


if __name__ == "__main__":
    app = create_ui()
    auth = (DASHBOARD_USERNAME, DASHBOARD_PASSWORD) if (DASHBOARD_USERNAME and DASHBOARD_PASSWORD) else None
    app.launch(server_name="0.0.0.0", server_port=7860, auth=auth, theme=gr.themes.Soft())