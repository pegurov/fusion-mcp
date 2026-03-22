"""
Fusion 360 MCP Server
Communicates with FusionMCPBridge add-in via file-based exchange.
Provides tools: execute_design, get_viewport, clear_design.
"""

import asyncio
import json
import os
import time
import uuid
import base64
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    ImageContent,
)
from camera_utils import fit_to_design

EXCHANGE_DIR = Path.home() / "fusion-mcp" / "exchange"
REQUEST_FILE = EXCHANGE_DIR / "request.json"
RESPONSE_FILE = EXCHANGE_DIR / "response.json"
RENDERS_DIR = EXCHANGE_DIR / "renders"

POLL_INTERVAL = 0.3  # seconds
TIMEOUT = 30  # seconds

server = Server("fusion-mcp")


async def _send_to_fusion(code: str, render: bool = True, timeout: float = TIMEOUT) -> dict:
    """Send a script to Fusion 360 and wait for response."""

    # Ensure exchange dir exists
    EXCHANGE_DIR.mkdir(parents=True, exist_ok=True)
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)

    # Clean stale response
    if RESPONSE_FILE.exists():
        RESPONSE_FILE.unlink()

    # Append camera setup if rendering
    if render:
        code = code + "\n" + fit_to_design()

    request_id = str(uuid.uuid4())[:8]
    request = {
        "id": request_id,
        "code": code,
        "render": render,
        "timestamp": time.time(),
    }

    # Write request atomically
    tmp = REQUEST_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, REQUEST_FILE)

    # Poll for response
    deadline = time.time() + timeout
    while time.time() < deadline:
        if RESPONSE_FILE.exists():
            try:
                data = RESPONSE_FILE.read_text(encoding="utf-8")
                response = json.loads(data)
                if response.get("id") == request_id:
                    RESPONSE_FILE.unlink(missing_ok=True)
                    return response
            except (json.JSONDecodeError, OSError):
                pass  # File still being written
        await asyncio.sleep(POLL_INTERVAL)

    return {
        "id": request_id,
        "success": False,
        "output": "",
        "error": f"Timeout after {timeout}s — is FusionMCPBridge add-in running in Fusion 360?",
        "render_path": None,
    }


def _read_image_as_base64(path: str | None) -> str | None:
    """Read an image file and return base64 encoded string."""
    if not path or not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("ascii")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="execute_design",
            description=(
                "Execute a Python script inside Fusion 360 and return the result + viewport render. "
                "The script runs with these globals available: app, ui, design, rootComp, adsk. "
                "Use Fusion 360 API (adsk.fusion, adsk.core) to create/modify geometry. "
                "Use print() to output messages."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute in Fusion 360. Has access to: app, ui, design, rootComp, adsk.",
                    },
                    "render": {
                        "type": "boolean",
                        "description": "Whether to capture a viewport screenshot after execution. Default true.",
                        "default": True,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in seconds. Default 30.",
                        "default": 30,
                    },
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="get_viewport",
            description="Capture and return the current Fusion 360 viewport as an image, without executing any code.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="clear_design",
            description="Remove all bodies and sketches from the active Fusion 360 design, giving a clean slate.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="inspect_design",
            description=(
                "Inspect the current Fusion 360 design and return a detailed report: "
                "all bodies (with bounding boxes, face/edge counts), sketches, timeline operations, "
                "user parameters, and component hierarchy. Use this to understand an existing model "
                "before modifying it. Returns text report + viewport screenshot."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    if name == "execute_design":
        code = arguments["code"]
        render = arguments.get("render", True)
        timeout = arguments.get("timeout", TIMEOUT)

        result = await _send_to_fusion(code, render=render, timeout=timeout)
        contents: list[TextContent | ImageContent] = []

        # Text result
        parts = []
        if result.get("output"):
            parts.append(f"Output:\n{result['output']}")
        if result.get("error"):
            parts.append(f"Error:\n{result['error']}")
        if not parts:
            parts.append("Script executed successfully (no output).")

        contents.append(TextContent(type="text", text="\n\n".join(parts)))

        # Image result
        if render and result.get("render_path"):
            img_data = _read_image_as_base64(result["render_path"])
            if img_data:
                contents.append(
                    ImageContent(
                        type="image",
                        data=img_data,
                        mimeType="image/png",
                    )
                )
                # Clean up render file
                try:
                    os.unlink(result["render_path"])
                except OSError:
                    pass

        return contents

    elif name == "get_viewport":
        code = "pass  # viewport capture only"
        result = await _send_to_fusion(code, render=True)
        contents = []

        if result.get("error"):
            contents.append(TextContent(type="text", text=f"Error: {result['error']}"))
        else:
            contents.append(TextContent(type="text", text="Current viewport:"))

        if result.get("render_path"):
            img_data = _read_image_as_base64(result["render_path"])
            if img_data:
                contents.append(
                    ImageContent(type="image", data=img_data, mimeType="image/png")
                )
                try:
                    os.unlink(result["render_path"])
                except OSError:
                    pass

        return contents

    elif name == "clear_design":
        clear_code = """
# Remove all occurrences (bodies) from root component
bodies = rootComp.bRepBodies
for i in range(bodies.count - 1, -1, -1):
    bodies.item(i).deleteMe()

# Remove all sketches
sketches = rootComp.sketches
for i in range(sketches.count - 1, -1, -1):
    sketches.item(i).deleteMe()

# Remove all occurrences (sub-components)
occs = rootComp.occurrences
for i in range(occs.count - 1, -1, -1):
    occs.item(i).deleteMe()

# Clear timeline
timeline = design.timeline
if timeline.count > 0:
    timeline.moveToBeginning()
    for i in range(timeline.count - 1, -1, -1):
        try:
            timeline.item(i).deleteMe()
        except:
            pass

print(f"Design cleared.")
"""
        result = await _send_to_fusion(clear_code, render=True)
        contents = []

        text = result.get("output", "") or ""
        if result.get("error"):
            text += f"\nError: {result['error']}"
        if not text.strip():
            text = "Design cleared."
        contents.append(TextContent(type="text", text=text))

        if result.get("render_path"):
            img_data = _read_image_as_base64(result["render_path"])
            if img_data:
                contents.append(
                    ImageContent(type="image", data=img_data, mimeType="image/png")
                )
                try:
                    os.unlink(result["render_path"])
                except OSError:
                    pass

        return contents

    elif name == "inspect_design":
        inspect_code = '''
import json as _json

_report = {}

# --- Design info ---
_report["name"] = design.rootComponent.name
_report["designType"] = "Parametric" if design.designType == adsk.fusion.DesignTypes.ParametricDesignType else "Direct"

# --- Component hierarchy ---
def _inspect_component(comp, depth=0):
    info = {
        "name": comp.name,
        "bodies": [],
        "sketches": [],
        "sub_components": [],
    }

    # Bodies
    for i in range(comp.bRepBodies.count):
        body = comp.bRepBodies.item(i)
        bb = body.boundingBox
        info["bodies"].append({
            "name": body.name,
            "visible": body.isVisible,
            "faces": body.faces.count,
            "edges": body.edges.count,
            "vertices": body.vertices.count,
            "boundingBox": {
                "min": [round(bb.minPoint.x, 3), round(bb.minPoint.y, 3), round(bb.minPoint.z, 3)],
                "max": [round(bb.maxPoint.x, 3), round(bb.maxPoint.y, 3), round(bb.maxPoint.z, 3)],
                "size": [
                    round(bb.maxPoint.x - bb.minPoint.x, 3),
                    round(bb.maxPoint.y - bb.minPoint.y, 3),
                    round(bb.maxPoint.z - bb.minPoint.z, 3),
                ],
            },
        })

    # Sketches
    for i in range(comp.sketches.count):
        sk = comp.sketches.item(i)
        info["sketches"].append({
            "name": sk.name,
            "profiles": sk.profiles.count,
            "curves": sk.sketchCurves.count,
            "plane": sk.referencePlane.name if hasattr(sk.referencePlane, "name") else str(sk.referencePlane.objectType),
        })

    # Sub-components
    for i in range(comp.occurrences.count):
        occ = comp.occurrences.item(i)
        info["sub_components"].append(_inspect_component(occ.component, depth + 1))

    return info

_report["rootComponent"] = _inspect_component(rootComp)

# --- Timeline ---
_timeline_ops = []
timeline = design.timeline
for i in range(min(timeline.count, 50)):  # cap at 50
    item = timeline.item(i)
    op = {"index": i, "name": item.name}
    try:
        entity = item.entity
        op["type"] = entity.objectType.split("::")[-1]
    except:
        op["type"] = "unknown"
    _timeline_ops.append(op)
_report["timeline"] = _timeline_ops
_report["timelineCount"] = timeline.count

# --- User parameters ---
_params = []
for i in range(design.userParameters.count):
    p = design.userParameters.item(i)
    _params.append({"name": p.name, "value": p.value, "unit": p.unit, "expression": p.expression})
_report["userParameters"] = _params

# --- Summary ---
total_bodies = sum(len(c["bodies"]) for c in [_report["rootComponent"]])
print(_json.dumps(_report, indent=2, ensure_ascii=False))
'''
        result = await _send_to_fusion(inspect_code, render=True)
        contents = []

        if result.get("output"):
            contents.append(TextContent(type="text", text=result["output"]))
        if result.get("error"):
            contents.append(TextContent(type="text", text=f"Error: {result['error']}"))
        if not contents:
            contents.append(TextContent(type="text", text="No design data returned."))

        if result.get("render_path"):
            img_data = _read_image_as_base64(result["render_path"])
            if img_data:
                contents.append(
                    ImageContent(type="image", data=img_data, mimeType="image/png")
                )
                try:
                    os.unlink(result["render_path"])
                except OSError:
                    pass

        return contents

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
