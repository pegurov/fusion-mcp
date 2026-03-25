"""
Fusion 360 MCP Server
Communicates with FusionMCPBridge add-in via file-based exchange.
Provides tools: execute_design, get_viewport, clear_design, inspect_design,
                undo, export_body, measure, section_view, api_docs, mesh_analyze.
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
        Tool(
            name="api_docs",
            description=(
                "Search the Fusion 360 API documentation via live introspection. "
                "Inspects adsk.core, adsk.fusion, and adsk.cam modules inside the running Fusion process. "
                "Returns matching classes, methods, properties with their signatures. "
                "Use this to discover API methods before writing code."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "search_term": {
                        "type": "string",
                        "description": "Class or member name to search for (e.g. 'Sketch', 'addByThreePoints', 'ExtrudeFeature').",
                    },
                    "class_name": {
                        "type": "string",
                        "description": "If provided, show all members of this specific class (e.g. 'BRepBody', 'Sketch').",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results. Default 20.",
                        "default": 20,
                    },
                },
            },
        ),
        Tool(
            name="mesh_analyze",
            description=(
                "Analyze a mesh body (imported STL/OBJ) in Fusion 360 without flooding the context. "
                "Returns a compact summary: bounding box, triangle/vertex count, volume, surface area, "
                "and detected features (holes, flat faces, cylindrical regions). "
                "Use this instead of inspect_design when working with mesh bodies to avoid context overflow."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "body_name": {
                        "type": "string",
                        "description": "Name of the mesh body to analyze. If omitted, analyzes all mesh bodies.",
                    },
                    "detect_features": {
                        "type": "boolean",
                        "description": "Run feature detection (holes, cylinders, flats). Slower but more useful. Default true.",
                        "default": True,
                    },
                    "sample_size": {
                        "type": "integer",
                        "description": "Max triangles to sample for feature detection. Default 5000. Higher = more accurate but slower.",
                        "default": 5000,
                    },
                    "min_radius": {
                        "type": "number",
                        "description": "Minimum hole radius in mm to report. Default 5. Set lower to see smaller features.",
                        "default": 5,
                    },
                    "min_circularity": {
                        "type": "number",
                        "description": "Minimum circularity score (0-1) to include in results. Default 0.5.",
                        "default": 0.5,
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

# --- Timeline (parametric designs only) ---
try:
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
except:
    _report["timeline"] = []
    _report["timelineCount"] = 0
    _report["designMode"] = "Direct (non-parametric)"

# --- User parameters ---
try:
    _params = []
    for i in range(design.userParameters.count):
        p = design.userParameters.item(i)
        _params.append({"name": p.name, "value": p.value, "unit": p.unit, "expression": p.expression})
    _report["userParameters"] = _params
except:
    _report["userParameters"] = []

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

    elif name == "api_docs":
        search_term = arguments.get("search_term", "")
        class_name = arguments.get("class_name", "")
        max_results = arguments.get("max_results", 20)

        docs_code = f"""
import inspect as _inspect

_search = "{search_term}"
_class_name = "{class_name}"
_max = {max_results}
_results = []

_MODULES = []
for _mod in [adsk.core, adsk.fusion]:
    _MODULES.append(_mod)
try:
    import adsk.cam
    _MODULES.append(adsk.cam)
except:
    pass

def _get_members(cls):
    members = []
    for name in sorted(dir(cls)):
        if name.startswith('_'):
            continue
        try:
            attr = getattr(cls, name)
        except:
            continue
        kind = "method" if callable(attr) else "property"
        sig = ""
        doc = ""
        if callable(attr):
            try:
                sig = str(_inspect.signature(attr))
            except (ValueError, TypeError):
                sig = "(...)"
        try:
            doc = (attr.__doc__ or "").split("\\n")[0][:120]
        except:
            pass
        members.append((name, kind, sig, doc))
    return members

if _class_name:
    # Show all members of a specific class
    _found = None
    for _mod in _MODULES:
        _cls = getattr(_mod, _class_name, None)
        if _cls is not None:
            _found = _cls
            break
    if _found is None:
        print(f"Class '{{_class_name}}' not found in adsk.core / adsk.fusion / adsk.cam")
    else:
        _mod_name = _found.__module__ if hasattr(_found, '__module__') else '?'
        print(f"=== {{_class_name}} ({{_mod_name}}) ===")
        _doc = (_found.__doc__ or "").split("\\n")[0][:200]
        if _doc:
            print(f"  {{_doc}}")
        print()
        _members = _get_members(_found)
        _props = [(n, s, d) for n, k, s, d in _members if k == "property"]
        _methods = [(n, s, d) for n, k, s, d in _members if k == "method"]
        if _props:
            print("Properties:")
            for n, s, d in _props[:_max]:
                line = f"  .{{n}}"
                if d:
                    line += f"  — {{d}}"
                print(line)
        if _methods:
            print("\\nMethods:")
            for n, s, d in _methods[:_max]:
                line = f"  .{{n}}{{s}}"
                if d:
                    line += f"  — {{d}}"
                print(line)
elif _search:
    # Search across all classes
    _search_lower = _search.lower()
    for _mod in _MODULES:
        _mod_prefix = _mod.__name__.split(".")[-1]
        for _name in sorted(dir(_mod)):
            if _name.startswith('_'):
                continue
            _cls = getattr(_mod, _name, None)
            if _cls is None or not isinstance(_cls, type):
                continue
            # Match class name
            if _search_lower in _name.lower():
                _doc = (_cls.__doc__ or "").split("\\n")[0][:120]
                _results.append(f"[class] {{_mod_prefix}}.{{_name}}  — {{_doc}}")
                continue
            # Match member names
            for _mname in dir(_cls):
                if _mname.startswith('_'):
                    continue
                if _search_lower in _mname.lower():
                    try:
                        _attr = getattr(_cls, _mname)
                        _kind = "method" if callable(_attr) else "prop"
                        _sig = ""
                        if callable(_attr):
                            try:
                                _sig = str(_inspect.signature(_attr))
                            except:
                                _sig = "(...)"
                        _doc = (_attr.__doc__ or "").split("\\n")[0][:100]
                        _results.append(f"[{{_kind}}] {{_mod_prefix}}.{{_name}}.{{_mname}}{{_sig}}  — {{_doc}}")
                    except:
                        _results.append(f"[?] {{_mod_prefix}}.{{_name}}.{{_mname}}")
                    break  # one match per class is enough
            if len(_results) >= _max:
                break
        if len(_results) >= _max:
            break
    if _results:
        print(f"Found {{len(_results)}} results for '{{_search}}':\\n")
        for r in _results[:_max]:
            print(r)
    else:
        print(f"No results for '{{_search}}'")
else:
    print("Provide search_term or class_name parameter.")
"""
        result = await _send_to_fusion(docs_code, render=False)
        contents = _build_response(result)
        return contents

    elif name == "mesh_analyze":
        body_name = arguments.get("body_name", "")
        detect_features = arguments.get("detect_features", True)
        sample_size = arguments.get("sample_size", 5000)
        min_radius = arguments.get("min_radius", 5)
        min_circularity = arguments.get("min_circularity", 0.5)

        analyze_code = f'''
import json as _json
import math as _math

_body_name = "{body_name}"
_detect_features = {detect_features}
_sample_size = {sample_size}
_min_radius = {min_radius}
_min_circularity = {min_circularity}

# Collect mesh bodies
_mesh_bodies = []
_brep_bodies = []

def _collect_bodies(comp, prefix=""):
    full_name = prefix + comp.name if prefix else comp.name
    # Mesh bodies
    if hasattr(comp, 'meshBodies'):
        for i in range(comp.meshBodies.count):
            mb = comp.meshBodies.item(i)
            if not _body_name or mb.name == _body_name:
                _mesh_bodies.append((mb, full_name))
    # BRep bodies (some imported STLs become BRep with many faces)
    for i in range(comp.bRepBodies.count):
        bb = comp.bRepBodies.item(i)
        if not _body_name or bb.name == _body_name:
            _brep_bodies.append((bb, full_name))
    # Recurse into sub-components
    for i in range(comp.occurrences.count):
        occ = comp.occurrences.item(i)
        _collect_bodies(occ.component, full_name + "/")

_collect_bodies(rootComp)

_report = {{"mesh_bodies": [], "brep_bodies": [], "summary": ""}}

# --- Analyze mesh bodies (MeshBody type) ---
for _mb, _comp_name in _mesh_bodies:
    _info = {{
        "name": _mb.name,
        "component": _comp_name,
        "type": "MeshBody",
    }}
    # Bounding box
    try:
        _bb = _mb.boundingBox
        _info["bounding_box"] = {{
            "min": [round(_bb.minPoint.x, 2), round(_bb.minPoint.y, 2), round(_bb.minPoint.z, 2)],
            "max": [round(_bb.maxPoint.x, 2), round(_bb.maxPoint.y, 2), round(_bb.maxPoint.z, 2)],
            "size": [
                round(_bb.maxPoint.x - _bb.minPoint.x, 2),
                round(_bb.maxPoint.y - _bb.minPoint.y, 2),
                round(_bb.maxPoint.z - _bb.minPoint.z, 2),
            ],
        }}
    except:
        pass
    # Mesh data via displayMesh
    try:
        _dm = _mb.displayMesh
        _info["triangles"] = _dm.triangleCount
        _info["vertices"] = _dm.nodeCount
    except:
        pass

    # --- Feature detection for MeshBody via boundary edges ---
    if _detect_features:
        try:
            _dm = _mb.displayMesh
            _coords = _dm.nodeCoordinatesAsFloat  # flat [x0,y0,z0, x1,y1,z1, ...]
            _indices = _dm.nodeIndices  # flat [t0v0,t0v1,t0v2, t1v0,...]
            _tri_count = len(_indices) // 3

            # Build edge count map — boundary edges appear only once
            _edge_count = {{}}
            for _ti in range(_tri_count):
                _i0 = _indices[_ti * 3]
                _i1 = _indices[_ti * 3 + 1]
                _i2 = _indices[_ti * 3 + 2]
                for _a, _b in [(_i0, _i1), (_i1, _i2), (_i2, _i0)]:
                    _edge = (_a, _b) if _a < _b else (_b, _a)
                    _edge_count[_edge] = _edge_count.get(_edge, 0) + 1

            _boundary = [e for e, c in _edge_count.items() if c == 1]
            _info["boundary_edges"] = len(_boundary)

            if _boundary:
                # Build adjacency graph from boundary edges
                _adj = {{}}
                for _a, _b in _boundary:
                    if _a not in _adj: _adj[_a] = []
                    if _b not in _adj: _adj[_b] = []
                    _adj[_a].append(_b)
                    _adj[_b].append(_a)

                # Find connected components (contours)
                _visited = set()
                _contours = []
                for _start in _adj:
                    if _start in _visited:
                        continue
                    _component = []
                    _stack = [_start]
                    while _stack:
                        _v = _stack.pop()
                        if _v in _visited:
                            continue
                        _visited.add(_v)
                        _component.append(_v)
                        for _n in _adj[_v]:
                            if _n not in _visited:
                                _stack.append(_n)
                    if len(_component) >= 3:
                        _contours.append(_component)

                # Analyze each contour — fit circle
                _holes = []
                for _cont in _contours:
                    _pts_x = [_coords[vi * 3] for vi in _cont]
                    _pts_y = [_coords[vi * 3 + 1] for vi in _cont]
                    _pts_z = [_coords[vi * 3 + 2] for vi in _cont]

                    _spread_x = max(_pts_x) - min(_pts_x)
                    _spread_y = max(_pts_y) - min(_pts_y)
                    _spread_z = max(_pts_z) - min(_pts_z)
                    _spreads = [("X", _spread_x), ("Y", _spread_y), ("Z", _spread_z)]
                    _spreads.sort(key=lambda s: s[1])

                    # Axis with smallest spread = hole axis
                    _hole_axis = _spreads[0][0]
                    _min_spread = _spreads[0][1]

                    # Use the other two axes for circle fitting
                    if _hole_axis == "X":
                        _u, _v_arr = _pts_y, _pts_z
                    elif _hole_axis == "Y":
                        _u, _v_arr = _pts_x, _pts_z
                    else:
                        _u, _v_arr = _pts_x, _pts_y

                    _cu = sum(_u) / len(_u)
                    _cv = sum(_v_arr) / len(_v_arr)

                    # Average radius
                    _radii = [_math.sqrt((_u[i] - _cu)**2 + (_v_arr[i] - _cv)**2) for i in range(len(_u))]
                    _avg_r = sum(_radii) / len(_radii)
                    _std_r = _math.sqrt(sum((r - _avg_r)**2 for r in _radii) / len(_radii))

                    # Circularity score: low std/mean = good circle
                    _circularity = 1.0 - min(_std_r / max(_avg_r, 0.01), 1.0)

                    # Center in 3D
                    if _hole_axis == "X":
                        _center_3d = [round(sum(_pts_x) / len(_pts_x), 2), round(_cu, 2), round(_cv, 2)]
                    elif _hole_axis == "Y":
                        _center_3d = [round(_cu, 2), round(sum(_pts_y) / len(_pts_y), 2), round(_cv, 2)]
                    else:
                        _center_3d = [round(_cu, 2), round(_cv, 2), round(sum(_pts_z) / len(_pts_z), 2)]

                    _holes.append({{
                        "vertices_in_contour": len(_cont),
                        "center_mm": _center_3d,
                        "radius_mm": round(_avg_r, 2),
                        "diameter_mm": round(_avg_r * 2, 2),
                        "axis": _hole_axis,
                        "circularity": round(_circularity, 3),
                        "spread_along_axis_mm": round(_min_spread, 2),
                        "type": "hole" if _circularity > 0.7 else "opening",
                    }})

                # --- Post-processing: filter, deduplicate, group, format ---
                _raw_count = len(_holes)

                # 1. Filter by min_radius, min_circularity, and min vertices
                _holes = [h for h in _holes
                          if h["radius_mm"] >= _min_radius
                          and h["circularity"] >= _min_circularity
                          and (h["vertices_in_contour"] >= 8 or h["circularity"] > 0.9)]

                # 2. Deduplicate: merge contours with similar center + radius (same hole, top/bottom/inner wall)
                _deduped = []
                for _h in _holes:
                    _merged = False
                    for _d in _deduped:
                        if _h["axis"] == _d["axis"] and abs(_h["radius_mm"] - _d["radius_mm"]) < 1.5:
                            _dist = _math.sqrt(sum((_h["center_mm"][i] - _d["center_mm"][i])**2 for i in range(3)))
                            if _dist < max(_h["radius_mm"] * 0.5, 5.0):
                                if _h["circularity"] > _d["circularity"]:
                                    _d["circularity"] = _h["circularity"]
                                _d["_layers"] = _d.get("_layers", 1) + 1
                                _merged = True
                                break
                    if not _merged:
                        _h["_layers"] = 1
                        _deduped.append(_h)

                # 3. Group by similar radius + axis + spatial proximity
                _deduped.sort(key=lambda h: (-h["circularity"], -h["radius_mm"]))
                _groups = []
                _used = set()
                for _i, _h in enumerate(_deduped):
                    if _i in _used:
                        continue
                    _group = [_h]
                    _used.add(_i)
                    for _j, _h2 in enumerate(_deduped):
                        if _j in _used:
                            continue
                        if _h["axis"] == _h2["axis"] and abs(_h["radius_mm"] - _h2["radius_mm"]) < 2.0:
                            # Only group if circularity is similar (both holes or both openings)
                            if abs(_h["circularity"] - _h2["circularity"]) < 0.3:
                                _group.append(_h2)
                                _used.add(_j)
                    _groups.append(_group)

                # 4. Sort groups: highest circularity first, then largest radius
                _groups.sort(key=lambda g: (-max(h["circularity"] for h in g), -g[0]["radius_mm"]))

                # 5. Format as compact text table
                _lines = []
                _lines.append(f"Detected {{len(_deduped)}} unique openings (from {{_raw_count}} raw contours)")
                _lines.append(f"Grouped into {{len(_groups)}} feature types:")
                _lines.append("")
                _lines.append(f"  {{'#':>3}} | {{'Count':>5}} | {{'Diam mm':>8}} | {{'Axis':>4}} | {{'Circ':>5}} | {{'Type':>7}} | Centers (mm)")
                _lines.append("  " + "-" * 80)
                for _gi, _g in enumerate(_groups):
                    _avg_r = sum(h["radius_mm"] for h in _g) / len(_g)
                    _best_circ = max(h["circularity"] for h in _g)
                    _ftype = "hole" if _best_circ > 0.7 else "opening"
                    # Format centers compactly
                    _centers_str = ""
                    for _ci, _c in enumerate(_g[:4]):
                        _cx, _cy, _cz = _c["center_mm"]
                        if _ci == 0:
                            _centers_str += f"[{{_cx:.0f}}, {{_cy:.0f}}, {{_cz:.0f}}]"
                        else:
                            _centers_str += f" [{{_cx:.0f}}, {{_cy:.0f}}, {{_cz:.0f}}]"
                    if len(_g) > 4:
                        _centers_str += f" +{{len(_g)-4}} more"
                    _lines.append(f"  {{_gi+1:>3}} | {{len(_g):>5}} | {{_avg_r*2:>8.1f}} | {{_g[0]['axis']:>4}} | {{_best_circ:>5.2f}} | {{_ftype:>7}} | {{_centers_str}}")
                _lines.append("")

                _info["analysis"] = "\\n".join(_lines)
                _info["feature_count"] = len(_groups)
                _info["unique_openings"] = len(_deduped)
                # Also keep structured data but minimal
                _info["features"] = []
                for _g in _groups:
                    _avg_r = sum(h["radius_mm"] for h in _g) / len(_g)
                    _best_circ = max(h["circularity"] for h in _g)
                    _info["features"].append({{
                        "count": len(_g),
                        "diameter_mm": round(_avg_r * 2, 1),
                        "axis": _g[0]["axis"],
                        "circularity": round(_best_circ, 2),
                        "centers_mm": [h["center_mm"] for h in _g[:4]],
                    }})
            else:
                _info["boundary_edges"] = 0
                _info["analysis"] = "Watertight mesh — no open boundaries detected"
                _info["features"] = []

            # --- Surface segmentation by normal direction ---
            _sample_n = min(_tri_count, _sample_size)
            _step = max(1, _tri_count // _sample_n)
            _THRESH = 0.7  # cos(45°) — threshold for axis alignment

            # Categories: top(Z+), bottom(Z-), front(Y-), back(Y+), left(X-), right(X+), angled
            _cats = {{
                "top_Zplus":    {{"count": 0, "area": 0.0, "xs": [], "ys": [], "zs": []}},
                "bottom_Zminus":{{"count": 0, "area": 0.0, "xs": [], "ys": [], "zs": []}},
                "front_Yminus": {{"count": 0, "area": 0.0, "xs": [], "ys": [], "zs": []}},
                "back_Yplus":   {{"count": 0, "area": 0.0, "xs": [], "ys": [], "zs": []}},
                "left_Xminus":  {{"count": 0, "area": 0.0, "xs": [], "ys": [], "zs": []}},
                "right_Xplus":  {{"count": 0, "area": 0.0, "xs": [], "ys": [], "zs": []}},
                "angled":       {{"count": 0, "area": 0.0, "xs": [], "ys": [], "zs": []}},
            }}

            for _si in range(0, _tri_count, _step):
                _i0 = _indices[_si * 3]
                _i1 = _indices[_si * 3 + 1]
                _i2 = _indices[_si * 3 + 2]
                _ax = _coords[_i0*3]; _ay = _coords[_i0*3+1]; _az = _coords[_i0*3+2]
                _bx = _coords[_i1*3]; _by = _coords[_i1*3+1]; _bz = _coords[_i1*3+2]
                _cx = _coords[_i2*3]; _cy = _coords[_i2*3+1]; _cz = _coords[_i2*3+2]

                # Normal via cross product
                _e1x = _bx-_ax; _e1y = _by-_ay; _e1z = _bz-_az
                _e2x = _cx-_ax; _e2y = _cy-_ay; _e2z = _cz-_az
                _nnx = _e1y*_e2z - _e1z*_e2y
                _nny = _e1z*_e2x - _e1x*_e2z
                _nnz = _e1x*_e2y - _e1y*_e2x
                _nlen = _math.sqrt(_nnx**2 + _nny**2 + _nnz**2)
                if _nlen == 0:
                    continue
                _nnx /= _nlen; _nny /= _nlen; _nnz /= _nlen

                # Triangle area (half cross product magnitude) in mm²
                _tri_area = _nlen * 0.5  # area in document units² (mm²)

                # Centroid in mm
                _cenx = (_ax + _bx + _cx) / 3.0
                _ceny = (_ay + _by + _cy) / 3.0
                _cenz = (_az + _bz + _cz) / 3.0

                # Classify by dominant normal direction
                if _nnz > _THRESH:
                    _cat = "top_Zplus"
                elif _nnz < -_THRESH:
                    _cat = "bottom_Zminus"
                elif _nny < -_THRESH:
                    _cat = "front_Yminus"
                elif _nny > _THRESH:
                    _cat = "back_Yplus"
                elif _nnx < -_THRESH:
                    _cat = "left_Xminus"
                elif _nnx > _THRESH:
                    _cat = "right_Xplus"
                else:
                    _cat = "angled"

                _c = _cats[_cat]
                _c["count"] += 1
                _c["area"] += _tri_area
                # Sample positions (keep max 200 per category for bbox)
                if len(_c["xs"]) < 200:
                    _c["xs"].append(_cenx)
                    _c["ys"].append(_ceny)
                    _c["zs"].append(_cenz)

            _sampled = sum(c["count"] for c in _cats.values())

            # Build surface segmentation report
            _seg_lines = []
            _seg_lines.append(f"Surface segmentation ({{_sampled}} triangles sampled):")
            _seg_lines.append("")

            _labels = {{
                "top_Zplus": "Top (Z+)",
                "bottom_Zminus": "Bottom (Z-)",
                "front_Yminus": "Front (Y-)",
                "back_Yplus": "Back (Y+)",
                "left_Xminus": "Left (X-)",
                "right_Xplus": "Right (X+)",
                "angled": "Angled/Curved",
            }}

            _seg_data = []
            for _key, _label in _labels.items():
                _c = _cats[_key]
                if _c["count"] == 0:
                    continue
                _pct = round(100 * _c["count"] / max(_sampled, 1), 1)
                _area_mm2 = _c["area"] * _step  # scale up from sampling
                _region = {{
                    "name": _label,
                    "pct": _pct,
                    "area_mm2": round(_area_mm2, 0),
                    "triangles": _c["count"] * _step,
                }}
                if _c["xs"]:
                    _region["bbox_mm"] = {{
                        "x": [round(min(_c["xs"]), 1), round(max(_c["xs"]), 1)],
                        "y": [round(min(_c["ys"]), 1), round(max(_c["ys"]), 1)],
                        "z": [round(min(_c["zs"]), 1), round(max(_c["zs"]), 1)],
                    }}
                _seg_data.append(_region)

                # Text line
                _bbox_str = ""
                if _c["xs"]:
                    _bbox_str = (f"X[{{min(_c['xs']):.0f}}..{{max(_c['xs']):.0f}}] "
                                 f"Y[{{min(_c['ys']):.0f}}..{{max(_c['ys']):.0f}}] "
                                 f"Z[{{min(_c['zs']):.0f}}..{{max(_c['zs']):.0f}}]")
                _seg_lines.append(f"  {{_label:>16}}: {{_pct:>5.1f}}%  ~{{_area_mm2:.0f}} mm²  {{_bbox_str}}")

            _info["surface_segmentation"] = "\\n".join(_seg_lines)
            _info["segments"] = _seg_data

        except Exception as _ex:
            _info["feature_detection_error"] = str(_ex)

    _report["mesh_bodies"].append(_info)

# --- Analyze BRep bodies (imported STL often becomes BRep) ---
for _bb_body, _comp_name in _brep_bodies:
    _info = {{
        "name": _bb_body.name,
        "component": _comp_name,
        "type": "BRepBody",
        "faces": _bb_body.faces.count,
        "edges": _bb_body.edges.count,
        "vertices": _bb_body.vertices.count,
    }}
    # Bounding box
    try:
        _bb = _bb_body.boundingBox
        _info["bounding_box"] = {{
            "min": [round(_bb.minPoint.x, 2), round(_bb.minPoint.y, 2), round(_bb.minPoint.z, 2)],
            "max": [round(_bb.maxPoint.x, 2), round(_bb.maxPoint.y, 2), round(_bb.maxPoint.z, 2)],
            "size": [
                round(_bb.maxPoint.x - _bb.minPoint.x, 2),
                round(_bb.maxPoint.y - _bb.minPoint.y, 2),
                round(_bb.maxPoint.z - _bb.minPoint.z, 2),
            ],
        }}
    except:
        pass
    # Physical properties
    try:
        _props = _bb_body.physicalProperties
        _info["volume_cm3"] = round(_props.volume, 4)
        _info["area_cm2"] = round(_props.area, 4)
    except:
        pass

    # Mesh triangle count (via mesh calculator)
    try:
        _mc = _bb_body.meshManager.createMeshCalculator()
        _mc.setQuality(adsk.fusion.TriangleMeshQualityOptions.NormalQualityTriangleMesh)
        _mesh = _mc.calculate()
        _info["triangles"] = _mesh.triangleCount
        _info["mesh_vertices"] = _mesh.nodeCount
    except:
        pass

    # --- Feature detection on BRep ---
    if _detect_features and _bb_body.faces.count < 50000:
        _features = []
        _face_count = _bb_body.faces.count

        # Classify faces by geometry type
        _planar = 0
        _cylindrical = []
        _conical = 0
        _spherical = 0
        _toroidal = 0
        _other = 0

        _limit = min(_face_count, _sample_size)
        for _fi in range(_limit):
            _face = _bb_body.faces.item(_fi)
            _geom = _face.geometry
            _gt = _geom.objectType

            if "Plane" in _gt:
                _planar += 1
            elif "Cylinder" in _gt:
                _cyl_geom = adsk.core.Cylinder.cast(_geom)
                if _cyl_geom:
                    _r = round(_cyl_geom.radius, 3)
                    _origin = _cyl_geom.origin
                    _axis_vec = _cyl_geom.axis
                    # Determine axis direction
                    _ax = "?"
                    _avx, _avy, _avz = abs(_axis_vec.x), abs(_axis_vec.y), abs(_axis_vec.z)
                    if _avz > _avx and _avz > _avy: _ax = "Z"
                    elif _avx > _avy: _ax = "X"
                    else: _ax = "Y"
                    _cylindrical.append({{
                        "radius_mm": _r,
                        "center_mm": [round(_origin.x, 2), round(_origin.y, 2), round(_origin.z, 2)],
                        "axis": _ax,
                    }})
            elif "Cone" in _gt:
                _conical += 1
            elif "Sphere" in _gt:
                _spherical += 1
            elif "Torus" in _gt:
                _toroidal += 1
            else:
                _other += 1

        _info["face_types"] = {{
            "planar": _planar,
            "cylindrical": len(_cylindrical),
            "conical": _conical,
            "spherical": _spherical,
            "toroidal": _toroidal,
            "other": _other,
            "sampled": _limit,
            "total": _face_count,
        }}

        # Group cylindrical faces by similar radius + position → holes/pins
        if _cylindrical:
            # Cluster by radius (within 0.1mm tolerance)
            _cyl_sorted = sorted(_cylindrical, key=lambda c: c["radius_mm"])
            _clusters = []
            _current = [_cyl_sorted[0]]
            for _c in _cyl_sorted[1:]:
                if abs(_c["radius_mm"] - _current[0]["radius_mm"]) < 0.1:
                    _current.append(_c)
                else:
                    _clusters.append(_current)
                    _current = [_c]
            _clusters.append(_current)

            _hole_groups = []
            for _cl in _clusters:
                _avg_r = sum(c["radius_mm"] for c in _cl) / len(_cl)
                # Deduplicate centers (within 1mm)
                _unique_centers = []
                for _c in _cl:
                    _is_dup = False
                    for _uc in _unique_centers:
                        _dist = _math.sqrt(sum((_c["center_mm"][i] - _uc[i])**2 for i in range(3)))
                        if _dist < 1.0:
                            _is_dup = True
                            break
                    if not _is_dup:
                        _unique_centers.append(_c["center_mm"])

                _hole_groups.append({{
                    "radius_mm": round(_avg_r, 2),
                    "diameter_mm": round(_avg_r * 2, 2),
                    "count": len(_unique_centers),
                    "axis": _cl[0]["axis"],
                    "centers_mm": _unique_centers[:10],  # cap at 10
                }})

            _info["cylindrical_features"] = _hole_groups

    _report["brep_bodies"].append(_info)

# Summary
_total = len(_report["mesh_bodies"]) + len(_report["brep_bodies"])
if _total == 0:
    if _body_name:
        print(f"Body '{{_body_name}}' not found.")
        print("Available bodies:")
        # List all bodies
        def _list_all(comp, indent=0):
            for i in range(comp.bRepBodies.count):
                print("  " * indent + f"  BRep: {{comp.bRepBodies.item(i).name}}")
            if hasattr(comp, 'meshBodies'):
                for i in range(comp.meshBodies.count):
                    print("  " * indent + f"  Mesh: {{comp.meshBodies.item(i).name}}")
            for i in range(comp.occurrences.count):
                occ = comp.occurrences.item(i)
                print("  " * indent + f"  Component: {{occ.component.name}}")
                _list_all(occ.component, indent + 1)
        _list_all(rootComp)
    else:
        print("No bodies found in current design.")
else:
    # Print compact summary for each body
    for _body_info in _report["mesh_bodies"] + _report["brep_bodies"]:
        print(f"=== {{_body_info['name']}} ({{_body_info['type']}}) ===")
        if "bounding_box" in _body_info:
            _sz = _body_info["bounding_box"]["size"]
            print(f"  Size: {{_sz[0]:.0f}} x {{_sz[1]:.0f}} x {{_sz[2]:.0f}} mm")
        if "triangles" in _body_info:
            print(f"  Triangles: {{_body_info['triangles']:,}}, Vertices: {{_body_info.get('vertices', _body_info.get('mesh_vertices', '?')):,}}")
        if "faces" in _body_info:
            print(f"  Faces: {{_body_info['faces']:,}}, Edges: {{_body_info['edges']:,}}")
        if "volume_cm3" in _body_info:
            print(f"  Volume: {{_body_info['volume_cm3']}} cm3, Area: {{_body_info['area_cm2']}} cm2")
        if "boundary_edges" in _body_info:
            print(f"  Boundary edges: {{_body_info['boundary_edges']:,}}")
        if "surface_segmentation" in _body_info:
            print()
            print(_body_info["surface_segmentation"])
        if "analysis" in _body_info:
            print()
            print(_body_info["analysis"])
        # Print structured features as compact JSON for programmatic use
        if _body_info.get("features"):
            print("--- features_json ---")
            print(_json.dumps(_body_info["features"], ensure_ascii=False))
        print()
'''
        result = await _send_to_fusion(analyze_code, render=True, timeout=60)
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
