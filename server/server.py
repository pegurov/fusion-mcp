"""
Fusion 360 MCP Server
Communicates with FusionMCPBridge add-in via file-based exchange.
Provides tools: execute_design, get_viewport, clear_design, inspect_design,
                undo, export_body, measure, section_view.
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
        Tool(
            name="undo",
            description=(
                "Undo the last N operations from the Fusion 360 timeline. "
                "Removes features from the end of the timeline, effectively rolling back changes. "
                "Use this to clean up after failed operations or to revert unwanted modifications."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of timeline operations to undo. Default 1.",
                        "default": 1,
                    },
                    "to_index": {
                        "type": "integer",
                        "description": "Alternatively, delete all timeline items after this index (exclusive). Overrides count.",
                    },
                },
            },
        ),
        Tool(
            name="export_body",
            description=(
                "Export a body from the Fusion 360 design as STL or 3MF file. "
                "Use this to prepare bodies for 3D printing or sharing."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "body_name": {
                        "type": "string",
                        "description": "Name of the body to export (e.g. 'Insert Top', 'External Frame').",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["stl", "3mf"],
                        "description": "Export format. Default 'stl'.",
                        "default": "stl",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Directory to save the file. Default: ~/Desktop.",
                    },
                    "refinement": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Mesh refinement level. Default 'high'.",
                        "default": "high",
                    },
                },
                "required": ["body_name"],
            },
        ),
        Tool(
            name="measure",
            description=(
                "Measure distances and dimensions in the Fusion 360 design. "
                "Can measure: gap between two bodies, body dimensions, "
                "or distance between a point and a body face."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "body_name": {
                        "type": "string",
                        "description": "Name of the body to measure.",
                    },
                    "body_name_2": {
                        "type": "string",
                        "description": "Second body name (for gap measurement between two bodies).",
                    },
                },
                "required": ["body_name"],
            },
        ),
        Tool(
            name="section_view",
            description=(
                "Create a section view of the design by cutting it with a plane. "
                "Useful for inspecting internal features like holes, recesses, and wall thickness. "
                "The section is temporary (analysis only, does not modify the design)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "axis": {
                        "type": "string",
                        "enum": ["x", "y", "z"],
                        "description": "Axis perpendicular to the section plane. Default 'y'.",
                        "default": "y",
                    },
                    "offset": {
                        "type": "number",
                        "description": "Offset of the section plane along the axis in cm. Default 0.",
                        "default": 0,
                    },
                },
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

    elif name == "undo":
        count = arguments.get("count", 1)
        to_index = arguments.get("to_index")

        if to_index is not None:
            undo_code = f"""
tl = design.timeline
deleted = 0
while tl.count > {to_index + 1}:
    try:
        item = tl.item(tl.count - 1)
        ent = item.entity
        name = ent.name if ent else "?"
        ent.deleteMe()
        deleted += 1
    except Exception as e:
        print(f"Failed to delete item: {{e}}")
        break
print(f"Undone {{deleted}} operations. Timeline now: {{tl.count}} items")
"""
        else:
            undo_code = f"""
tl = design.timeline
deleted = 0
for _ in range({count}):
    if tl.count == 0:
        break
    try:
        item = tl.item(tl.count - 1)
        ent = item.entity
        name = ent.name if ent else "?"
        ent.deleteMe()
        deleted += 1
    except Exception as e:
        print(f"Failed to delete item: {{e}}")
        break
print(f"Undone {{deleted}} operations. Timeline now: {{tl.count}} items")
"""
        result = await _send_to_fusion(undo_code, render=True)
        contents = _build_response(result)
        return contents

    elif name == "export_body":
        body_name = arguments["body_name"]
        fmt = arguments.get("format", "stl")
        output_dir = arguments.get("output_dir", str(Path.home() / "Desktop"))
        refinement = arguments.get("refinement", "high")

        refinement_map = {
            "low": "adsk.fusion.MeshRefinementSettings.MeshRefinementLow",
            "medium": "adsk.fusion.MeshRefinementSettings.MeshRefinementMedium",
            "high": "adsk.fusion.MeshRefinementSettings.MeshRefinementHigh",
        }
        ref_enum = refinement_map.get(refinement, refinement_map["high"])

        export_code = f"""
import os
body = None
for i in range(rootComp.bRepBodies.count):
    b = rootComp.bRepBodies.item(i)
    if b.name == "{body_name}":
        body = b
        break

if not body:
    print(f"ERROR: Body '{body_name}' not found. Available bodies:")
    for i in range(rootComp.bRepBodies.count):
        print(f"  - {{rootComp.bRepBodies.item(i).name}}")
else:
    output_dir = r"{output_dir}"
    os.makedirs(output_dir, exist_ok=True)
    safe_name = "{body_name}".replace(" ", "_").replace("/", "_")
    filepath = os.path.join(output_dir, f"{{safe_name}}.{fmt}")

    exportMgr = design.exportManager
    if "{fmt}" == "stl":
        opts = exportMgr.createSTLExportOptions(body, filepath)
        opts.meshRefinement = {ref_enum}
        exportMgr.execute(opts)
    elif "{fmt}" == "3mf":
        # 3MF export requires the full design, not individual body
        # Export as STL instead with a note
        filepath = os.path.join(output_dir, f"{{safe_name}}.stl")
        opts = exportMgr.createSTLExportOptions(body, filepath)
        opts.meshRefinement = {ref_enum}
        exportMgr.execute(opts)
        print("Note: Individual body 3MF export not supported. Exported as STL.")

    if os.path.exists(filepath):
        size_kb = os.path.getsize(filepath) / 1024
        print(f"Exported: {{filepath}} ({{size_kb:.0f}} KB)")
    else:
        print(f"ERROR: Export failed — file not created at {{filepath}}")
"""
        result = await _send_to_fusion(export_code, render=False)
        contents = _build_response(result)
        return contents

    elif name == "measure":
        body_name = arguments["body_name"]
        body_name_2 = arguments.get("body_name_2")

        if body_name_2:
            measure_code = f"""
b1 = b2 = None
for i in range(rootComp.bRepBodies.count):
    b = rootComp.bRepBodies.item(i)
    if b.name == "{body_name}": b1 = b
    if b.name == "{body_name_2}": b2 = b

if not b1: print(f"ERROR: Body '{body_name}' not found")
elif not b2: print(f"ERROR: Body '{body_name_2}' not found")
else:
    bb1 = b1.boundingBox; bb2 = b2.boundingBox
    print(f"=== {{b1.name}} ===")
    print(f"  Size: {{(bb1.maxPoint.x-bb1.minPoint.x)*10:.1f}} x {{(bb1.maxPoint.y-bb1.minPoint.y)*10:.1f}} x {{(bb1.maxPoint.z-bb1.minPoint.z)*10:.1f}} mm")
    print(f"  X: [{{bb1.minPoint.x*10:.1f}}, {{bb1.maxPoint.x*10:.1f}}] mm")
    print(f"  Y: [{{bb1.minPoint.y*10:.1f}}, {{bb1.maxPoint.y*10:.1f}}] mm")
    print(f"  Z: [{{bb1.minPoint.z*10:.1f}}, {{bb1.maxPoint.z*10:.1f}}] mm")
    print(f"  Faces: {{b1.faces.count}}, Edges: {{b1.edges.count}}")
    print(f"\\n=== {{b2.name}} ===")
    print(f"  Size: {{(bb2.maxPoint.x-bb2.minPoint.x)*10:.1f}} x {{(bb2.maxPoint.y-bb2.minPoint.y)*10:.1f}} x {{(bb2.maxPoint.z-bb2.minPoint.z)*10:.1f}} mm")
    print(f"  X: [{{bb2.minPoint.x*10:.1f}}, {{bb2.maxPoint.x*10:.1f}}] mm")
    print(f"  Y: [{{bb2.minPoint.y*10:.1f}}, {{bb2.maxPoint.y*10:.1f}}] mm")
    print(f"  Z: [{{bb2.minPoint.z*10:.1f}}, {{bb2.maxPoint.z*10:.1f}}] mm")
    print(f"  Faces: {{b2.faces.count}}, Edges: {{b2.edges.count}}")
    # Gap analysis (axis-aligned bounding box gaps)
    gaps = {{}}
    for axis, a1min, a1max, a2min, a2max in [
        ("X", bb1.minPoint.x, bb1.maxPoint.x, bb2.minPoint.x, bb2.maxPoint.x),
        ("Y", bb1.minPoint.y, bb1.maxPoint.y, bb2.minPoint.y, bb2.maxPoint.y),
        ("Z", bb1.minPoint.z, bb1.maxPoint.z, bb2.minPoint.z, bb2.maxPoint.z),
    ]:
        if a1max < a2min:
            gaps[axis] = (a2min - a1max) * 10
        elif a2max < a1min:
            gaps[axis] = (a1min - a2max) * 10
        else:
            overlap = min(a1max, a2max) - max(a1min, a2min)
            gaps[axis] = -overlap * 10  # negative = overlap
    print(f"\\n=== Gap Analysis ===")
    for axis, gap in gaps.items():
        if gap > 0:
            print(f"  {{axis}}: {{gap:.2f}} mm gap")
        else:
            print(f"  {{axis}}: {{-gap:.2f}} mm overlap")
"""
        else:
            measure_code = f"""
body = None
for i in range(rootComp.bRepBodies.count):
    b = rootComp.bRepBodies.item(i)
    if b.name == "{body_name}": body = b; break

if not body:
    print(f"ERROR: Body '{body_name}' not found")
else:
    bb = body.boundingBox
    print(f"=== {{body.name}} ===")
    print(f"  Size: {{(bb.maxPoint.x-bb.minPoint.x)*10:.2f}} x {{(bb.maxPoint.y-bb.minPoint.y)*10:.2f}} x {{(bb.maxPoint.z-bb.minPoint.z)*10:.2f}} mm")
    print(f"  X: [{{bb.minPoint.x*10:.2f}}, {{bb.maxPoint.x*10:.2f}}] mm")
    print(f"  Y: [{{bb.minPoint.y*10:.2f}}, {{bb.maxPoint.y*10:.2f}}] mm")
    print(f"  Z: [{{bb.minPoint.z*10:.2f}}, {{bb.maxPoint.z*10:.2f}}] mm")
    print(f"  Faces: {{body.faces.count}}")
    print(f"  Edges: {{body.edges.count}}")
    print(f"  Vertices: {{body.vertices.count}}")
    # Physical properties
    try:
        props = body.physicalProperties
        print(f"  Volume: {{props.volume:.4f}} cm³ ({{props.volume*1000:.1f}} mm³)")
        print(f"  Area: {{props.area:.4f}} cm² ({{props.area*100:.1f}} mm²)")
    except: pass
    # Wall analysis (min/max distances between outer and inner faces)
    print(f"\\n  Wall thickness (X): {{(bb.maxPoint.x - bb.minPoint.x)*10/2:.2f}} mm half-width")
"""
        result = await _send_to_fusion(measure_code, render=False)
        contents = _build_response(result)
        return contents

    elif name == "section_view":
        axis = arguments.get("axis", "y")
        offset = arguments.get("offset", 0)

        axis_map = {
            "x": ("rootComp.yZConstructionPlane", offset),
            "y": ("rootComp.xZConstructionPlane", offset),
            "z": ("rootComp.xYConstructionPlane", offset),
        }
        plane_ref, off = axis_map.get(axis, axis_map["y"])

        section_code = f"""
import adsk.core, adsk.fusion

# Create or reuse section analysis
analyses = rootComp.features.sectionAnalyses if hasattr(rootComp.features, 'sectionAnalyses') else None

# Use construction plane with offset
ref_plane = {plane_ref}
offset_val = adsk.core.ValueInput.createByReal({off})
plane_input = rootComp.constructionPlanes.createInput()
plane_input.setByOffset(ref_plane, offset_val)
section_plane = rootComp.constructionPlanes.add(plane_input)

# Set camera to look along the section axis for a clear view
_vp = app.activeViewport
_cam = _vp.camera
_cam.isFitView = True
_cam.isSmoothTransition = False

axis = "{axis}"
if axis == "x":
    _cam.eye = adsk.core.Point3D.create(30, 0, 5)
    _cam.target = adsk.core.Point3D.create(0, 0, 0.75)
elif axis == "y":
    _cam.eye = adsk.core.Point3D.create(0, 30, 5)
    _cam.target = adsk.core.Point3D.create(0, 0, 0.75)
else:
    _cam.eye = adsk.core.Point3D.create(15, 15, 30)
    _cam.target = adsk.core.Point3D.create(0, 0, 0.75)
_cam.upVector = adsk.core.Vector3D.create(0, 0, 1)
_vp.camera = _cam
_vp.fit()
adsk.doEvents()

print(f"Section plane created on {{axis.upper()}} axis at offset {{{off}*10:.1f}}mm")
print("Note: Enable Section Analysis in Fusion UI (INSPECT > Section Analysis) using this plane.")
print("The construction plane has been created — select it for section analysis.")
"""
        result = await _send_to_fusion(section_code, render=True)
        contents = _build_response(result)
        return contents

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


def _build_response(result: dict) -> list[TextContent | ImageContent]:
    """Build standard response contents from a Fusion execution result."""
    contents: list[TextContent | ImageContent] = []
    parts = []
    if result.get("output"):
        parts.append(result["output"])
    if result.get("error"):
        parts.append(f"Error: {result['error']}")
    if not parts:
        parts.append("Done.")
    contents.append(TextContent(type="text", text="\n".join(parts)))

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


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
