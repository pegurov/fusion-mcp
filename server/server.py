"""
Fusion 360 MCP Server
Communicates with FusionMCPBridge add-in via file-based exchange.
Provides tools: execute_design, get_viewport, clear_design, inspect_design,
                undo, export_body, measure, section_view, api_docs,
                mesh_analyze, mesh_modify, highlight.
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
                "Analyze a mesh body (STL file or Fusion mesh) without flooding the context. "
                "Returns: bounding box, triangle/vertex count, detected features (holes, circular openings), "
                "and surface segmentation by normal direction. "
                "Prefer stl_path for direct STL analysis (faster, no Fusion needed). "
                "Use body_name only when the mesh is already loaded in Fusion and not saved as STL."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "stl_path": {
                        "type": "string",
                        "description": "Path to STL file. If provided, analysis runs in pure Python (fast, no Fusion needed).",
                    },
                    "body_name": {
                        "type": "string",
                        "description": "Name of mesh body in Fusion. Used only if stl_path is not provided.",
                    },
                    "detect_features": {
                        "type": "boolean",
                        "description": "Run feature detection (holes, cylinders, flats). Default true.",
                        "default": True,
                    },
                    "sample_size": {
                        "type": "integer",
                        "description": "Max triangles to sample for surface segmentation. Default 5000.",
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
        Tool(
            name="mesh_modify",
            description=(
                "Modify a mesh (STL file) using pure Python — no Fusion needed, won't crash. "
                "Supports radial_displacement: shrink/expand cylindrical holes by moving vertices "
                "at a given radius from a center axis to a new radius. "
                "Use mesh_analyze first to find feature centers and radii, then pass them here. "
                "The center coordinates come from mesh_analyze's feature groups (divide by 10 if "
                "mesh_analyze bug ×10 is still active)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "stl_input": {
                        "type": "string",
                        "description": "Path to input STL file.",
                    },
                    "stl_output": {
                        "type": "string",
                        "description": "Path to output STL file. Default: ~/Desktop/modified.stl.",
                    },
                    "operation": {
                        "type": "string",
                        "enum": ["radial_displacement"],
                        "description": "Modification operation to apply.",
                    },
                    "axis": {
                        "type": "string",
                        "enum": ["X", "Y", "Z"],
                        "description": "Cylinder axis direction. Displacement happens in the perpendicular plane.",
                        "default": "X",
                    },
                    "center": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Center of the cylinder in the perpendicular plane [coord1, coord2]. For axis=X: [center_Y, center_Z]. For axis=Y: [center_X, center_Z]. For axis=Z: [center_X, center_Y].",
                    },
                    "current_radius": {
                        "type": "number",
                        "description": "Current radius of the cylindrical surface to modify (mm).",
                    },
                    "target_radius": {
                        "type": "number",
                        "description": "Desired new radius (mm).",
                    },
                    "tolerance": {
                        "type": "number",
                        "description": "Radius matching tolerance in mm. Default 0.05. Use tight values (0.03-0.05) for meshes with concentric surfaces.",
                        "default": 0.05,
                    },
                },
                "required": ["stl_input", "operation", "center", "current_radius", "target_radius"],
            },
        ),
        Tool(
            name="highlight",
            description=(
                "Place a temporary visual marker in Fusion 360 to highlight a point or feature. "
                "Use this to verify you're looking at the correct feature before modifying it. "
                "Creates a small colored sphere or ring at the specified position."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "position": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "3D position [X, Y, Z] in document units (mm) where to place the marker.",
                    },
                    "radius": {
                        "type": "number",
                        "description": "Marker radius in mm. Default 2.",
                        "default": 2,
                    },
                    "label": {
                        "type": "string",
                        "description": "Optional label to print identifying this marker.",
                    },
                    "clear": {
                        "type": "boolean",
                        "description": "If true, remove all previous highlight markers before placing new one.",
                        "default": False,
                    },
                },
                "required": ["position"],
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
        stl_path = arguments.get("stl_path", "")
        body_name = arguments.get("body_name", "")
        detect_features = arguments.get("detect_features", True)
        sample_size = arguments.get("sample_size", 5000)
        min_radius = arguments.get("min_radius", 5)
        min_circularity = arguments.get("min_circularity", 0.5)

        if stl_path:
            # --- Pure Python path: read STL directly ---
            return _mesh_analyze_stl(
                stl_path, detect_features, sample_size, min_radius, min_circularity
            )
        else:
            # --- Fusion path: use displayMesh (legacy) ---
            result = await _send_to_fusion(
                _mesh_analyze_fusion_code(body_name, detect_features, sample_size, min_radius, min_circularity),
                render=True, timeout=60,
            )
            contents = _build_response(result)
            return contents

    elif name == "mesh_modify":
        import struct
        import math

        stl_input = arguments["stl_input"]
        stl_output = arguments.get("stl_output", str(Path.home() / "Desktop" / "modified.stl"))
        operation = arguments["operation"]
        axis = arguments.get("axis", "X")
        center = arguments["center"]
        current_radius = arguments["current_radius"]
        target_radius = arguments["target_radius"]
        tolerance = arguments.get("tolerance", 0.05)

        try:
            # Read binary STL
            with open(stl_input, "rb") as f:
                header = f.read(80)
                n_tris = struct.unpack("<I", f.read(4))[0]
                triangles = []
                for _ in range(n_tris):
                    nx, ny, nz = struct.unpack("<fff", f.read(12))
                    v0 = list(struct.unpack("<fff", f.read(12)))
                    v1 = list(struct.unpack("<fff", f.read(12)))
                    v2 = list(struct.unpack("<fff", f.read(12)))
                    attr = struct.unpack("<H", f.read(2))[0]
                    triangles.append(([nx, ny, nz], v0, v1, v2, attr))

            # Build unique vertex map (by coordinate → list of mutable refs)
            vert_map: dict[tuple, list] = {}
            for tri in triangles:
                for v in [tri[1], tri[2], tri[3]]:
                    key = (round(v[0], 6), round(v[1], 6), round(v[2], 6))
                    if key not in vert_map:
                        vert_map[key] = []
                    vert_map[key].append(v)

            # Axis mapping: which coordinate indices form the perpendicular plane
            axis_map = {
                "X": (1, 2),  # perpendicular plane is YZ
                "Y": (0, 2),  # perpendicular plane is XZ
                "Z": (0, 1),  # perpendicular plane is XY
            }
            idx_u, idx_v = axis_map[axis]
            c_u, c_v = center[0], center[1]

            if operation == "radial_displacement":
                modified = 0
                for (vx, vy, vz), vrefs in vert_map.items():
                    coords = [vx, vy, vz]
                    du = coords[idx_u] - c_u
                    dv = coords[idx_v] - c_v
                    dist = math.sqrt(du * du + dv * dv)
                    if abs(dist - current_radius) < tolerance:
                        scale = target_radius / dist
                        new_u = c_u + du * scale
                        new_v = c_v + dv * scale
                        for vref in vrefs:
                            vref[idx_u] = new_u
                            vref[idx_v] = new_v
                        modified += 1

            # Write output STL
            with open(stl_output, "wb") as f:
                out_header = f"Modified: {operation} r={current_radius}->{target_radius}".encode()
                f.write(out_header.ljust(80, b"\0"))
                f.write(struct.pack("<I", n_tris))
                for normal, v0, v1, v2, attr in triangles:
                    f.write(struct.pack("<fff", *normal))
                    f.write(struct.pack("<fff", *v0))
                    f.write(struct.pack("<fff", *v1))
                    f.write(struct.pack("<fff", *v2))
                    f.write(struct.pack("<H", attr))

            out_size = os.path.getsize(stl_output) / (1024 * 1024)
            text = (
                f"Modified {modified} unique vertex positions\n"
                f"Operation: {operation} (axis={axis}, center={center}, "
                f"r={current_radius} -> {target_radius}, tolerance={tolerance})\n"
                f"Input: {stl_input} ({n_tris} triangles, {len(vert_map)} unique vertices)\n"
                f"Output: {stl_output} ({out_size:.1f} MB)"
            )
            return [TextContent(type="text", text=text)]

        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    elif name == "highlight":
        position = arguments["position"]
        radius = arguments.get("radius", 2)
        label = arguments.get("label", "")
        clear = arguments.get("clear", False)

        px, py, pz = position[0], position[1], position[2]
        label_str = label or f"({px:.1f}, {py:.1f}, {pz:.1f})"

        # Fusion API uses cm internally; mesh displayMesh uses document units (mm).
        # For mesh bodies coordinates are in mm, so we convert to cm for Fusion API.
        # The sphere center and camera target use cm.
        cx, cy, cz = px / 10.0, py / 10.0, pz / 10.0
        r_cm = radius / 10.0

        highlight_code = f"""
import adsk.core, adsk.fusion

# Clear previous markers
if {clear}:
    deleted = 0
    for i in range(rootComp.bRepBodies.count - 1, -1, -1):
        b = rootComp.bRepBodies.item(i)
        if b.name.startswith("_marker_"):
            b.deleteMe()
            deleted += 1
    if deleted:
        print(f"Cleared {{deleted}} previous markers")

# Create sphere via TemporaryBRepManager
tbm = adsk.fusion.TemporaryBRepManager.get()
center = adsk.core.Point3D.create({cx}, {cy}, {cz})
sphere = tbm.createSphere(center, {r_cm})

# In Direct Design, need BaseFeature to add BRep body
try:
    bf = rootComp.features.baseFeatures.add()
    bf.startEdit()
    body = rootComp.bRepBodies.add(sphere, bf)
    body.name = "_marker_{label_str.replace(' ', '_')[:20]}"
    body.opacity = 0.4
    bf.finishEdit()
    print(f"Marker sphere at [{px:.1f}, {py:.1f}, {pz:.1f}] r={radius}mm")
except Exception as e:
    # Fallback: try without BaseFeature (parametric mode)
    try:
        body = rootComp.bRepBodies.add(sphere)
        body.name = "_marker_{label_str.replace(' ', '_')[:20]}"
        print(f"Marker sphere at [{px:.1f}, {py:.1f}, {pz:.1f}] r={radius}mm")
    except Exception as e2:
        print(f"Could not create marker: {{e2}}")

print(f"Label: {label_str}")

# Point camera at the marker for close-up view
_vp = app.activeViewport
_cam = _vp.camera
_cam.isSmoothTransition = False
_cam.target = adsk.core.Point3D.create({cx}, {cy}, {cz})
_cam.eye = adsk.core.Point3D.create({cx} + 3, {cy} - 3, {cz} + 2)
_cam.upVector = adsk.core.Vector3D.create(0, 0, 1)
_vp.camera = _cam
adsk.doEvents()

print("Camera pointed at marker. Use clear=true to remove.")
"""
        result = await _send_to_fusion(highlight_code, render=True)
        contents = _build_response(result)
        return contents

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


def _mesh_analyze_stl(
    stl_path: str,
    detect_features: bool,
    sample_size: int,
    min_radius: float,
    min_circularity: float,
) -> list[TextContent]:
    """Analyze an STL file using pure Python — no Fusion needed."""
    import struct
    import math
    import json

    stl_path = os.path.expanduser(stl_path)
    with open(stl_path, "rb") as f:
        header = f.read(80)
        n_tris = struct.unpack("<I", f.read(4))[0]

        # Read all triangles: normals + 3 vertices each
        tri_normals = []  # [(nx, ny, nz), ...]
        tri_verts = []  # [(v0, v1, v2), ...] where vi = (x, y, z)
        for _ in range(n_tris):
            nx, ny, nz = struct.unpack("<fff", f.read(12))
            v0 = struct.unpack("<fff", f.read(12))
            v1 = struct.unpack("<fff", f.read(12))
            v2 = struct.unpack("<fff", f.read(12))
            f.read(2)  # attribute
            tri_normals.append((nx, ny, nz))
            tri_verts.append((v0, v1, v2))

    # Build indexed mesh: merge coincident vertices
    vert_to_idx: dict[tuple, int] = {}
    coords: list[float] = []  # flat [x0,y0,z0, x1,y1,z1, ...]
    indices: list[int] = []  # flat [t0v0,t0v1,t0v2, ...]

    for v0, v1, v2 in tri_verts:
        for v in (v0, v1, v2):
            key = (round(v[0], 5), round(v[1], 5), round(v[2], 5))
            if key not in vert_to_idx:
                vert_to_idx[key] = len(vert_to_idx)
                coords.extend(key)
            indices.append(vert_to_idx[key])

    n_verts = len(vert_to_idx)
    tri_count = len(tri_verts)

    # Bounding box
    xs = coords[0::3]
    ys = coords[1::3]
    zs = coords[2::3]
    bb_min = [min(xs), min(ys), min(zs)]
    bb_max = [max(xs), max(ys), max(zs)]
    bb_size = [bb_max[i] - bb_min[i] for i in range(3)]

    lines = []
    lines.append(f"=== {os.path.basename(stl_path)} (STL, pure Python) ===")
    lines.append(f"  Size: {bb_size[0]:.1f} x {bb_size[1]:.1f} x {bb_size[2]:.1f} mm")
    lines.append(f"  Triangles: {tri_count:,}, Vertices: {n_verts:,}")

    # --- Surface segmentation ---
    sample_n = min(tri_count, sample_size)
    step = max(1, tri_count // sample_n)
    THRESH = 0.7

    cats = {
        "top_Zplus": {"count": 0, "area": 0.0, "xs": [], "ys": [], "zs": []},
        "bottom_Zminus": {"count": 0, "area": 0.0, "xs": [], "ys": [], "zs": []},
        "front_Yminus": {"count": 0, "area": 0.0, "xs": [], "ys": [], "zs": []},
        "back_Yplus": {"count": 0, "area": 0.0, "xs": [], "ys": [], "zs": []},
        "left_Xminus": {"count": 0, "area": 0.0, "xs": [], "ys": [], "zs": []},
        "right_Xplus": {"count": 0, "area": 0.0, "xs": [], "ys": [], "zs": []},
        "angled": {"count": 0, "area": 0.0, "xs": [], "ys": [], "zs": []},
    }

    for si in range(0, tri_count, step):
        v0, v1, v2 = tri_verts[si]
        e1 = (v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2])
        e2 = (v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2])
        nnx = e1[1] * e2[2] - e1[2] * e2[1]
        nny = e1[2] * e2[0] - e1[0] * e2[2]
        nnz = e1[0] * e2[1] - e1[1] * e2[0]
        nlen = math.sqrt(nnx**2 + nny**2 + nnz**2)
        if nlen == 0:
            continue
        nnx /= nlen; nny /= nlen; nnz /= nlen
        tri_area = nlen * 0.5
        cenx = (v0[0] + v1[0] + v2[0]) / 3
        ceny = (v0[1] + v1[1] + v2[1]) / 3
        cenz = (v0[2] + v1[2] + v2[2]) / 3

        if nnz > THRESH: cat = "top_Zplus"
        elif nnz < -THRESH: cat = "bottom_Zminus"
        elif nny < -THRESH: cat = "front_Yminus"
        elif nny > THRESH: cat = "back_Yplus"
        elif nnx < -THRESH: cat = "left_Xminus"
        elif nnx > THRESH: cat = "right_Xplus"
        else: cat = "angled"

        c = cats[cat]
        c["count"] += 1
        c["area"] += tri_area
        if len(c["xs"]) < 200:
            c["xs"].append(cenx); c["ys"].append(ceny); c["zs"].append(cenz)

    sampled = sum(c["count"] for c in cats.values())
    labels = {
        "top_Zplus": "Top (Z+)", "bottom_Zminus": "Bottom (Z-)",
        "front_Yminus": "Front (Y-)", "back_Yplus": "Back (Y+)",
        "left_Xminus": "Left (X-)", "right_Xplus": "Right (X+)",
        "angled": "Angled/Curved",
    }
    lines.append(f"\nSurface segmentation ({sampled} triangles sampled):\n")
    for key, label in labels.items():
        c = cats[key]
        if c["count"] == 0:
            continue
        pct = round(100 * c["count"] / max(sampled, 1), 1)
        area = c["area"] * step
        bbox = ""
        if c["xs"]:
            bbox = (f"X[{min(c['xs']):.0f}..{max(c['xs']):.0f}] "
                    f"Y[{min(c['ys']):.0f}..{max(c['ys']):.0f}] "
                    f"Z[{min(c['zs']):.0f}..{max(c['zs']):.0f}]")
        lines.append(f"  {label:>16}: {pct:>5.1f}%  ~{area:.0f} mm²  {bbox}")

    # --- Feature detection via boundary edges ---
    features_json = []
    if detect_features:
        edge_count: dict[tuple, int] = {}
        for ti in range(tri_count):
            i0, i1, i2 = indices[ti * 3], indices[ti * 3 + 1], indices[ti * 3 + 2]
            for a, b in ((i0, i1), (i1, i2), (i2, i0)):
                edge = (a, b) if a < b else (b, a)
                edge_count[edge] = edge_count.get(edge, 0) + 1

        boundary = [e for e, c in edge_count.items() if c == 1]
        lines.append(f"\n  Boundary edges: {len(boundary):,}")

        if boundary:
            adj: dict[int, list[int]] = {}
            for a, b in boundary:
                adj.setdefault(a, []).append(b)
                adj.setdefault(b, []).append(a)

            visited: set[int] = set()
            contours: list[list[int]] = []
            for start in adj:
                if start in visited:
                    continue
                comp = []
                stack = [start]
                while stack:
                    v = stack.pop()
                    if v in visited:
                        continue
                    visited.add(v)
                    comp.append(v)
                    for n in adj[v]:
                        if n not in visited:
                            stack.append(n)
                if len(comp) >= 3:
                    contours.append(comp)

            holes = []
            for cont in contours:
                pts_x = [coords[vi * 3] for vi in cont]
                pts_y = [coords[vi * 3 + 1] for vi in cont]
                pts_z = [coords[vi * 3 + 2] for vi in cont]

                spreads = sorted(
                    [("X", max(pts_x) - min(pts_x)),
                     ("Y", max(pts_y) - min(pts_y)),
                     ("Z", max(pts_z) - min(pts_z))],
                    key=lambda s: s[1],
                )
                hole_axis = spreads[0][0]

                if hole_axis == "X": u, v_arr = pts_y, pts_z
                elif hole_axis == "Y": u, v_arr = pts_x, pts_z
                else: u, v_arr = pts_x, pts_y

                cu = sum(u) / len(u)
                cv = sum(v_arr) / len(v_arr)
                radii = [math.sqrt((u[i] - cu)**2 + (v_arr[i] - cv)**2) for i in range(len(u))]
                avg_r = sum(radii) / len(radii)
                std_r = math.sqrt(sum((r - avg_r)**2 for r in radii) / len(radii))
                circularity = 1.0 - min(std_r / max(avg_r, 0.01), 1.0)

                if hole_axis == "X":
                    center_3d = [round(sum(pts_x) / len(pts_x), 2), round(cu, 2), round(cv, 2)]
                elif hole_axis == "Y":
                    center_3d = [round(cu, 2), round(sum(pts_y) / len(pts_y), 2), round(cv, 2)]
                else:
                    center_3d = [round(cu, 2), round(cv, 2), round(sum(pts_z) / len(pts_z), 2)]

                holes.append({
                    "vertices_in_contour": len(cont), "center_mm": center_3d,
                    "radius_mm": round(avg_r, 2), "axis": hole_axis,
                    "circularity": round(circularity, 3),
                })

            raw_count = len(holes)
            holes = [h for h in holes
                     if h["radius_mm"] >= min_radius
                     and h["circularity"] >= min_circularity
                     and (h["vertices_in_contour"] >= 8 or h["circularity"] > 0.9)]

            # Deduplicate
            deduped = []
            for h in holes:
                merged = False
                for d in deduped:
                    if h["axis"] == d["axis"] and abs(h["radius_mm"] - d["radius_mm"]) < 1.5:
                        dist = math.sqrt(sum((h["center_mm"][i] - d["center_mm"][i])**2 for i in range(3)))
                        if dist < max(h["radius_mm"] * 0.5, 5.0):
                            if h["circularity"] > d["circularity"]:
                                d["circularity"] = h["circularity"]
                            merged = True
                            break
                if not merged:
                    deduped.append(h)

            # Group
            deduped.sort(key=lambda h: (-h["circularity"], -h["radius_mm"]))
            groups = []
            used: set[int] = set()
            for i, h in enumerate(deduped):
                if i in used:
                    continue
                group = [h]
                used.add(i)
                for j, h2 in enumerate(deduped):
                    if j in used:
                        continue
                    if h["axis"] == h2["axis"] and abs(h["radius_mm"] - h2["radius_mm"]) < 2.0:
                        if abs(h["circularity"] - h2["circularity"]) < 0.3:
                            group.append(h2)
                            used.add(j)
                groups.append(group)
            groups.sort(key=lambda g: (-max(h["circularity"] for h in g), -g[0]["radius_mm"]))

            lines.append(f"\nDetected {len(deduped)} unique openings (from {raw_count} raw contours)")
            lines.append(f"Grouped into {len(groups)} feature types:\n")
            lines.append(f"  {'#':>3} | {'Count':>5} | {'Diam mm':>8} | {'Axis':>4} | {'Circ':>5} | {'Type':>7} | Centers (mm)")
            lines.append("  " + "-" * 80)
            for gi, g in enumerate(groups):
                avg_r = sum(h["radius_mm"] for h in g) / len(g)
                best_circ = max(h["circularity"] for h in g)
                ftype = "hole" if best_circ > 0.7 else "opening"
                centers_str = ""
                for ci, c in enumerate(g[:4]):
                    cx, cy, cz = c["center_mm"]
                    if ci == 0:
                        centers_str += f"[{cx:.0f}, {cy:.0f}, {cz:.0f}]"
                    else:
                        centers_str += f" [{cx:.0f}, {cy:.0f}, {cz:.0f}]"
                if len(g) > 4:
                    centers_str += f" +{len(g) - 4} more"
                lines.append(f"  {gi+1:>3} | {len(g):>5} | {avg_r*2:>8.1f} | {g[0]['axis']:>4} | {best_circ:>5.2f} | {ftype:>7} | {centers_str}")

            for g in groups:
                avg_r = sum(h["radius_mm"] for h in g) / len(g)
                best_circ = max(h["circularity"] for h in g)
                features_json.append({
                    "count": len(g), "diameter_mm": round(avg_r * 2, 1),
                    "axis": g[0]["axis"], "circularity": round(best_circ, 2),
                    "centers_mm": [h["center_mm"] for h in g[:4]],
                })
        else:
            lines.append("  Watertight mesh — no open boundaries")

    if features_json:
        lines.append("\n--- features_json ---")
        lines.append(json.dumps(features_json, ensure_ascii=False))

    return [TextContent(type="text", text="\n".join(lines))]


def _mesh_analyze_fusion_code(
    body_name: str, detect_features: bool, sample_size: int,
    min_radius: float, min_circularity: float,
) -> str:
    """Return the Fusion-side Python script for mesh_analyze (legacy path)."""
    return f'''
import json as _json
import math as _math

_body_name = "{body_name}"
_detect_features = {detect_features}
_sample_size = {sample_size}
_min_radius = {min_radius}
_min_circularity = {min_circularity}

_mesh_bodies = []
_brep_bodies = []

def _collect_bodies(comp, prefix=""):
    full_name = prefix + comp.name if prefix else comp.name
    if hasattr(comp, 'meshBodies'):
        for i in range(comp.meshBodies.count):
            mb = comp.meshBodies.item(i)
            if not _body_name or mb.name == _body_name:
                _mesh_bodies.append((mb, full_name))
    for i in range(comp.bRepBodies.count):
        bb = comp.bRepBodies.item(i)
        if not _body_name or bb.name == _body_name:
            _brep_bodies.append((bb, full_name))
    for i in range(comp.occurrences.count):
        occ = comp.occurrences.item(i)
        _collect_bodies(occ.component, full_name + "/")

_collect_bodies(rootComp)

if not _mesh_bodies and not _brep_bodies:
    if _body_name:
        print(f"Body '{{_body_name}}' not found.")
    else:
        print("No bodies found. Use stl_path parameter for direct STL analysis.")
else:
    for _mb, _comp_name in _mesh_bodies:
        _dm = _mb.displayMesh
        print(f"=== {{_mb.name}} (MeshBody via Fusion) ===")
        print(f"  Triangles: {{_dm.triangleCount:,}}, Vertices: {{_dm.nodeCount:,}}")
        _bb = _mb.boundingBox
        print(f"  BBox: [{{_bb.minPoint.x:.1f}}, {{_bb.minPoint.y:.1f}}, {{_bb.minPoint.z:.1f}}] to [{{_bb.maxPoint.x:.1f}}, {{_bb.maxPoint.y:.1f}}, {{_bb.maxPoint.z:.1f}}]")
        print("  Note: Use stl_path for full analysis (features, segmentation)")
    for _bb_body, _comp_name in _brep_bodies:
        print(f"=== {{_bb_body.name}} (BRepBody) ===")
        print(f"  Faces: {{_bb_body.faces.count:,}}, Edges: {{_bb_body.edges.count:,}}")
'''


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
