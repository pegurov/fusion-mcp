"""
FusionMCPBridge Add-in
Polls exchange/ folder for script requests from Claude MCP server,
executes them in Fusion 360 context, captures viewport render.
"""

import adsk.core
import adsk.fusion
import json
import os
import traceback
import time
import threading

# Globals
_app: adsk.core.Application = None
_ui: adsk.core.UserInterface = None
_handlers = []
_stop_event = threading.Event()
_poll_thread: threading.Thread = None
_custom_event: adsk.core.CustomEvent = None

EXCHANGE_DIR = os.path.join(os.path.expanduser("~"), "fusion-mcp", "exchange")
REQUEST_FILE = os.path.join(EXCHANGE_DIR, "request.json")
RESPONSE_FILE = os.path.join(EXCHANGE_DIR, "response.json")
RENDERS_DIR = os.path.join(EXCHANGE_DIR, "renders")

EVENT_ID = "FusionMCPBridgeExecuteEvent"


class ExecuteEventHandler(adsk.core.CustomEventHandler):
    """Handles script execution on Fusion's main thread via CustomEvent."""

    def __init__(self):
        super().__init__()

    def notify(self, args: adsk.core.CustomEventArgs):
        try:
            request = json.loads(args.additionalInfo)
            request_id = request.get("id", "unknown")
            script_code = request.get("code", "")
            do_render = request.get("render", True)

            # Prepare execution context with Fusion API available
            app = adsk.core.Application.get()
            design = adsk.fusion.Design.cast(app.activeProduct)

            exec_globals = {
                "adsk": adsk,
                "app": app,
                "ui": app.userInterface,
                "design": design,
                "rootComp": design.rootComponent if design else None,
            }

            # Capture print output
            output_lines = []
            original_print = __builtins__["print"] if isinstance(__builtins__, dict) else __builtins__.print

            def capture_print(*a, **kw):
                output_lines.append(" ".join(str(x) for x in a))

            exec_globals["print"] = capture_print

            # Execute the script
            error = None
            try:
                exec(script_code, exec_globals)
            except Exception as e:
                error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

            # Capture viewport render
            render_path = None
            if do_render:
                try:
                    render_path = os.path.join(
                        RENDERS_DIR, f"render_{request_id}.png"
                    )
                    app.activeViewport.saveAsImageFile(render_path, 1920, 1080)
                except Exception as re:
                    if error is None:
                        error = ""
                    error += f"\nRender error: {re}"

            # Write response
            response = {
                "id": request_id,
                "success": error is None,
                "output": "\n".join(output_lines),
                "error": error,
                "render_path": render_path,
                "timestamp": time.time(),
            }

            # Write atomically: write to tmp then rename
            tmp_path = RESPONSE_FILE + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(response, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, RESPONSE_FILE)

        except Exception as ex:
            # Last resort error handling
            try:
                response = {
                    "id": "error",
                    "success": False,
                    "output": "",
                    "error": f"Bridge error: {ex}\n{traceback.format_exc()}",
                    "render_path": None,
                    "timestamp": time.time(),
                }
                tmp_path = RESPONSE_FILE + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(response, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, RESPONSE_FILE)
            except:
                pass


def _poll_loop(custom_event_id: str):
    """Background thread: polls for request.json, fires CustomEvent on main thread."""
    app = adsk.core.Application.get()

    while not _stop_event.is_set():
        try:
            if os.path.exists(REQUEST_FILE):
                with open(REQUEST_FILE, "r", encoding="utf-8") as f:
                    request_data = f.read()

                # Remove request file immediately to avoid re-processing
                os.remove(REQUEST_FILE)

                # Fire custom event on main thread
                app.fireCustomEvent(custom_event_id, request_data)
        except Exception:
            pass

        _stop_event.wait(0.5)  # Poll every 500ms


def run(context):
    global _app, _ui, _handlers, _poll_thread, _custom_event

    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface

        # Ensure exchange directories exist
        os.makedirs(RENDERS_DIR, exist_ok=True)

        # Clean up stale files
        for f in [REQUEST_FILE, RESPONSE_FILE]:
            if os.path.exists(f):
                os.remove(f)

        # Register custom event
        _custom_event = _app.registerCustomEvent(EVENT_ID)
        handler = ExecuteEventHandler()
        _custom_event.add(handler)
        _handlers.append(handler)

        # Start polling thread
        _stop_event.clear()
        _poll_thread = threading.Thread(target=_poll_loop, args=(EVENT_ID,), daemon=True)
        _poll_thread.start()

        _ui.messageBox("FusionMCPBridge started.\nListening for scripts from Claude.")

    except Exception:
        if _ui:
            _ui.messageBox(f"FusionMCPBridge failed to start:\n{traceback.format_exc()}")


def stop(context):
    global _poll_thread, _custom_event

    try:
        # Stop polling
        _stop_event.set()
        if _poll_thread:
            _poll_thread.join(timeout=2)
            _poll_thread = None

        # Unregister event
        if _custom_event:
            _app.unregisterCustomEvent(EVENT_ID)
            _custom_event = None

        _handlers.clear()

    except Exception:
        pass
