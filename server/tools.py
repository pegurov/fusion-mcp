"""
Hot-reloadable tool definitions and handlers for Fusion 360 MCP Server.

This module is loaded dynamically by server.py on every call — edits take
effect immediately without restarting the MCP server.
"""

import os
import json
import struct
import math
from pathlib import Path

from mcp.types import Tool, TextContent, ImageContent


# ---------------------------------------------------------------------------
#  Tool definitions
# ---------------------------------------------------------------------------

def get_tool_definitions() -> list[Tool]:
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
                "The section is temporary (analysis only, does not modify the design). "
                "Use focus_point to zoom into a specific area of the section."
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
                    "focus_point": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Optional [X, Y, Z] in mm to center the camera on. Zooms into this area for close-up inspection.",
                    },
                    "view_extent": {
                        "type": "number",
                        "description": "Camera view extent in cm (zoom level). Smaller = closer. Default auto-fit.",
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
                "Returns: bounding box, triangle/vertex count, surface segmentation, "
                "cylindrical features (holes/tubes via curvature analysis — works on watertight meshes), "
                "flat walls (with coordinates for planar_shift), cross-section scan (through-holes & channels), "
                "and feature relationships (distances between cylinders). "
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
                "Operations:\n"
                "  radial_displacement: shrink/expand cylindrical holes by moving vertices "
                "at a given radius from a center axis to a new radius.\n"
                "  planar_shift: move flat wall vertices from one coordinate to another along an axis. "
                "Use to widen/narrow rectangular slots, adjust wall positions, etc."
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
                        "enum": ["radial_displacement", "planar_shift"],
                        "description": "Modification operation to apply.",
                    },
                    "axis": {
                        "type": "string",
                        "enum": ["X", "Y", "Z"],
                        "description": "For radial_displacement: cylinder axis. For planar_shift: axis perpendicular to the wall.",
                        "default": "X",
                    },
                    "center": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "radial_displacement only: center in the perpendicular plane [coord1, coord2].",
                    },
                    "current_radius": {
                        "type": "number",
                        "description": "radial_displacement only: current radius of the cylindrical surface (mm).",
                    },
                    "target_radius": {
                        "type": "number",
                        "description": "radial_displacement only: desired new radius (mm).",
                    },
                    "coordinate_value": {
                        "type": "number",
                        "description": "planar_shift only: current coordinate of the wall along the axis (mm).",
                    },
                    "target_value": {
                        "type": "number",
                        "description": "planar_shift only: desired new coordinate for the wall (mm).",
                    },
                    "tolerance": {
                        "type": "number",
                        "description": "Matching tolerance in mm. Default 0.05.",
                        "default": 0.05,
                    },
                },
                "required": ["stl_input", "operation"],
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
        Tool(
            name="import_mesh",
            description=(
                "Import an STL file into the active Fusion 360 design as a mesh body. "
                "Parses binary STL, deduplicates vertices, converts mm→cm, and loads via addByTriangleMeshData. "
                "Much faster than writing STL-parsing code via execute_design every time."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "stl_path": {
                        "type": "string",
                        "description": "Path to the STL file to import.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Name for the imported mesh body. Default: filename without extension.",
                    },
                    "scale": {
                        "type": "number",
                        "description": "Scale factor. Default 1.0 (assumes STL is in mm).",
                        "default": 1.0,
                    },
                },
                "required": ["stl_path"],
            },
        ),
        Tool(
            name="open_file",
            description=(
                "Open a .f3d file from disk in Fusion 360. "
                "Imports the file into a new document and activates it. "
                "Closes all other documents first for a clean state."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the .f3d file.",
                    },
                    "close_others": {
                        "type": "boolean",
                        "description": "Close all other documents before opening. Default true.",
                        "default": True,
                    },
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="init_reconstruction",
            description=(
                "Set up two-tab workflow for reconstruction. "
                "Records the current document as 'original', creates a new empty 'Reconstruction' document, "
                "and activates it. Use this before starting step-by-step reconstruction."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="switch_document",
            description=(
                "Switch between the original and reconstruction documents. "
                "Use role='original' to view the original, role='reconstruction' to work on the rebuild."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": ["original", "reconstruction"],
                        "description": "Which document to activate.",
                    },
                },
                "required": ["role"],
            },
        ),
        Tool(
            name="reconstruct_step",
            description=(
                "Execute a single reconstruction step in the reconstruction document. "
                "Loads the step snapshot, generates code via generator, executes in Fusion, "
                "and saves the successful code for regression testing."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "step_index": {
                        "type": "integer",
                        "description": "Timeline step index to reconstruct.",
                    },
                },
                "required": ["step_index"],
            },
        ),
        Tool(
            name="regression_test",
            description=(
                "Re-run all reconstruction steps from scratch and verify each one. "
                "Clears the reconstruction document, executes all saved step codes sequentially, "
                "and runs verify_step after each. Returns a summary of which steps pass/fail."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "up_to_step": {
                        "type": "integer",
                        "description": "Only test up to this step index (inclusive). Default: all saved steps.",
                    },
                    "regenerate": {
                        "type": "boolean",
                        "description": "If true, regenerate step codes using current generator instead of replaying saved codes. Default false.",
                    },
                },
            },
        ),
        Tool(
            name="export_step_snapshots",
            description=(
                "Capture ground truth metrics from the ORIGINAL design at each timeline step. "
                "Rolls through the timeline, extracting body count, volume, area, bbox, face/edge counts "
                "at each step. Saves per-step JSON snapshots to exchange/snapshots/. "
                "Run this on the original design BEFORE starting reconstruction."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "max_steps": {
                        "type": "integer",
                        "description": "Max timeline steps to capture. Default 0 = all steps.",
                        "default": 0,
                    },
                },
            },
        ),
        Tool(
            name="verify_step",
            description=(
                "Verify the current reconstruction state against ground truth for a given step. "
                "Extracts metrics from the current design and compares against cached snapshot. "
                "Uses EXACT matching with no tolerances — either PASS or FAIL."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "step_index": {
                        "type": "integer",
                        "description": "Timeline step index to verify against.",
                    },
                },
                "required": ["step_index"],
            },
        ),
        Tool(
            name="design_to_python",
            description=(
                "Export the current parametric Fusion 360 design as a Python script. "
                "Rolls through the timeline step by step, inspecting selected profiles, edges, "
                "and faces IN CONTEXT to capture geometric descriptions (not internal IDs). "
                "Returns a self-contained Python script that recreates the design from scratch. "
                "Only works with parametric designs that have a timeline."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "output_path": {
                        "type": "string",
                        "description": "Path to save the generated Python script. Default: ~/Desktop/design_export.py.",
                    },
                },
            },
        ),
    ]


# ---------------------------------------------------------------------------
#  Tool handler dispatch
# ---------------------------------------------------------------------------

TIMEOUT = 30

async def handle_tool(
    name: str,
    arguments: dict,
    send_to_fusion,
    read_image_as_base64,
) -> list[TextContent | ImageContent]:
    """Dispatch tool calls. Called by server.py on every invocation."""

    if name == "execute_design":
        return await _handle_execute_design(arguments, send_to_fusion, read_image_as_base64)
    elif name == "get_viewport":
        return await _handle_get_viewport(send_to_fusion, read_image_as_base64)
    elif name == "clear_design":
        return await _handle_clear_design(send_to_fusion, read_image_as_base64)
    elif name == "inspect_design":
        return await _handle_inspect_design(send_to_fusion, read_image_as_base64)
    elif name == "undo":
        return await _handle_undo(arguments, send_to_fusion, read_image_as_base64)
    elif name == "export_body":
        return await _handle_export_body(arguments, send_to_fusion, read_image_as_base64)
    elif name == "measure":
        return await _handle_measure(arguments, send_to_fusion, read_image_as_base64)
    elif name == "section_view":
        return await _handle_section_view(arguments, send_to_fusion, read_image_as_base64)
    elif name == "api_docs":
        return await _handle_api_docs(arguments, send_to_fusion, read_image_as_base64)
    elif name == "mesh_analyze":
        return await _handle_mesh_analyze(arguments, send_to_fusion, read_image_as_base64)
    elif name == "mesh_modify":
        return _handle_mesh_modify(arguments)
    elif name == "highlight":
        return await _handle_highlight(arguments, send_to_fusion, read_image_as_base64)
    elif name == "import_mesh":
        return await _handle_import_mesh(arguments, send_to_fusion, read_image_as_base64)
    elif name == "open_file":
        return await _handle_open_file(arguments, send_to_fusion, read_image_as_base64)
    elif name == "init_reconstruction":
        return await _handle_init_reconstruction(send_to_fusion, read_image_as_base64)
    elif name == "switch_document":
        return await _handle_switch_document(arguments, send_to_fusion, read_image_as_base64)
    elif name == "reconstruct_step":
        return await _handle_reconstruct_step(arguments, send_to_fusion, read_image_as_base64)
    elif name == "regression_test":
        return await _handle_regression_test(arguments, send_to_fusion, read_image_as_base64)
    elif name == "export_step_snapshots":
        return await _handle_export_step_snapshots(arguments, send_to_fusion, read_image_as_base64)
    elif name == "verify_step":
        return await _handle_verify_step(arguments, send_to_fusion, read_image_as_base64)
    elif name == "design_to_python":
        return await _handle_design_to_python(arguments, send_to_fusion, read_image_as_base64)
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ---------------------------------------------------------------------------
#  Shared helpers
# ---------------------------------------------------------------------------

def _build_response(result: dict, read_image_as_base64) -> list[TextContent | ImageContent]:
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
        img_data = read_image_as_base64(result["render_path"])
        if img_data:
            contents.append(
                ImageContent(type="image", data=img_data, mimeType="image/png")
            )
            try:
                os.unlink(result["render_path"])
            except OSError:
                pass

    return contents


def _generate_reconstruction_script(data: dict) -> str:
    """Dynamically load and run the generator from generator.py (hot-reloadable)."""
    import importlib
    gen_path = os.path.join(os.path.dirname(__file__), 'generator.py')
    spec = importlib.util.spec_from_file_location("generator", gen_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._generate_reconstruction_script(data)


# ---------------------------------------------------------------------------
#  Individual tool handlers
# ---------------------------------------------------------------------------

async def _handle_execute_design(arguments, send_to_fusion, read_image_as_base64):
    code = arguments["code"]
    render = arguments.get("render", True)
    timeout = arguments.get("timeout", TIMEOUT)

    result = await send_to_fusion(code, render=render, timeout=timeout)
    contents: list[TextContent | ImageContent] = []

    parts = []
    if result.get("output"):
        parts.append(f"Output:\n{result['output']}")
    if result.get("error"):
        parts.append(f"Error:\n{result['error']}")
    if not parts:
        parts.append("Script executed successfully (no output).")

    contents.append(TextContent(type="text", text="\n\n".join(parts)))

    if render and result.get("render_path"):
        img_data = read_image_as_base64(result["render_path"])
        if img_data:
            contents.append(
                ImageContent(type="image", data=img_data, mimeType="image/png")
            )
            try:
                os.unlink(result["render_path"])
            except OSError:
                pass

    return contents


async def _handle_get_viewport(send_to_fusion, read_image_as_base64):
    code = "pass  # viewport capture only"
    result = await send_to_fusion(code, render=True)
    contents = []

    if result.get("error"):
        contents.append(TextContent(type="text", text=f"Error: {result['error']}"))
    else:
        contents.append(TextContent(type="text", text="Current viewport:"))

    if result.get("render_path"):
        img_data = read_image_as_base64(result["render_path"])
        if img_data:
            contents.append(
                ImageContent(type="image", data=img_data, mimeType="image/png")
            )
            try:
                os.unlink(result["render_path"])
            except OSError:
                pass

    return contents


async def _handle_clear_design(send_to_fusion, read_image_as_base64):
    clear_code = """
# Delete timeline items from END to START (avoids dependency issues)
timeline = design.timeline
_deleted = 0
for i in range(timeline.count - 1, -1, -1):
    try:
        timeline.item(i).entity.deleteMe()
        _deleted += 1
    except:
        pass

# Clean up any remaining bodies/sketches/occurrences
for i in range(rootComp.occurrences.count - 1, -1, -1):
    try: rootComp.occurrences.item(i).deleteMe()
    except: pass
for i in range(rootComp.bRepBodies.count - 1, -1, -1):
    try: rootComp.bRepBodies.item(i).deleteMe()
    except: pass
for i in range(rootComp.sketches.count - 1, -1, -1):
    try: rootComp.sketches.item(i).deleteMe()
    except: pass
for i in range(rootComp.constructionPlanes.count - 1, -1, -1):
    try: rootComp.constructionPlanes.item(i).deleteMe()
    except: pass

print(f"Design cleared. Deleted {_deleted} timeline items. Remaining: {design.timeline.count}")
"""
    result = await send_to_fusion(clear_code, render=True)
    contents = []

    text = result.get("output", "") or ""
    if result.get("error"):
        text += f"\nError: {result['error']}"
    if not text.strip():
        text = "Design cleared."
    contents.append(TextContent(type="text", text=text))

    if result.get("render_path"):
        img_data = read_image_as_base64(result["render_path"])
        if img_data:
            contents.append(
                ImageContent(type="image", data=img_data, mimeType="image/png")
            )
            try:
                os.unlink(result["render_path"])
            except OSError:
                pass

    return contents


async def _handle_inspect_design(send_to_fusion, read_image_as_base64):
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
    result = await send_to_fusion(inspect_code, render=True)
    contents = []

    if result.get("output"):
        contents.append(TextContent(type="text", text=result["output"]))
    if result.get("error"):
        contents.append(TextContent(type="text", text=f"Error: {result['error']}"))
    if not contents:
        contents.append(TextContent(type="text", text="No design data returned."))

    if result.get("render_path"):
        img_data = read_image_as_base64(result["render_path"])
        if img_data:
            contents.append(
                ImageContent(type="image", data=img_data, mimeType="image/png")
            )
            try:
                os.unlink(result["render_path"])
            except OSError:
                pass

    return contents


async def _ensure_document_role(role: str, send_to_fusion):
    """Ensure the active Fusion document matches the expected role.
    Identifies documents by CONTENT (body names, timeline count), not by name.
    Returns (ok, message)."""
    # Check current document by _recon marker parameter
    check_code = '''
_has_recon = False
try:
    _has_recon = (design.userParameters.itemByName("_recon") is not None)
except:
    pass
_is_original = not _has_recon
print(f"__IS_ORIGINAL__={'1' if _is_original else '0'}")
print(f"__DOC_COUNT__={app.documents.count}")
'''
    result = await send_to_fusion(check_code, render=False, timeout=10)
    output = result.get("output", "")
    is_original = False
    doc_count = 1
    for line in output.split("\n"):
        if line.startswith("__IS_ORIGINAL__="):
            is_original = line.split("=", 1)[1].strip() == "1"
        if line.startswith("__DOC_COUNT__="):
            doc_count = int(line.split("=", 1)[1].strip())

    # Check if already on the right document
    if (role == "original" and is_original) or (role == "reconstruction" and not is_original):
        return True, ""

    if doc_count < 2:
        return False, f"Only {doc_count} document open. Run init_reconstruction first."

    # Wrong document — switch using content-based identification
    pipeline = _hot_reload_module('pipeline')
    switch_code = pipeline.get_switch_document_code(role)
    switch_result = await send_to_fusion(switch_code, render=False, timeout=10)
    switch_output = switch_result.get("output", "")
    if "ERROR" in switch_output:
        return False, f"Failed to switch to {role}: {switch_output}"
    return True, f"Auto-switched to {role}"


async def _handle_undo(arguments, send_to_fusion, read_image_as_base64):
    # Safety: ensure we're on reconstruction document
    ok, msg = await _ensure_document_role("reconstruction", send_to_fusion)
    if not ok:
        return [TextContent(type="text", text=f"Document switch failed: {msg}")]
    if msg:
        pass  # auto-switched document
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
    result = await send_to_fusion(undo_code, render=True)
    return _build_response(result, read_image_as_base64)


async def _handle_export_body(arguments, send_to_fusion, read_image_as_base64):
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
    result = await send_to_fusion(export_code, render=False)
    return _build_response(result, read_image_as_base64)


async def _handle_measure(arguments, send_to_fusion, read_image_as_base64):
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
    result = await send_to_fusion(measure_code, render=False)
    return _build_response(result, read_image_as_base64)


async def _handle_section_view(arguments, send_to_fusion, read_image_as_base64):
    axis = arguments.get("axis", "y")
    offset = arguments.get("offset", 0)
    focus_point = arguments.get("focus_point", None)
    view_extent = arguments.get("view_extent", None)

    axis_map = {
        "x": ("rootComp.yZConstructionPlane", offset),
        "y": ("rootComp.xZConstructionPlane", offset),
        "z": ("rootComp.xYConstructionPlane", offset),
    }
    plane_ref, off = axis_map.get(axis, axis_map["y"])

    if focus_point:
        fp_cm = [focus_point[0] / 10.0, focus_point[1] / 10.0, focus_point[2] / 10.0]
        target_code = f"adsk.core.Point3D.create({fp_cm[0]}, {fp_cm[1]}, {fp_cm[2]})"
    else:
        target_code = "adsk.core.Point3D.create(0, 0, 0.75)"

    cam_dist = 15

    extent_code = ""
    if view_extent:
        extent_code = f"""
_cam.isFitView = False
_cam.viewExtents = {view_extent}
"""
    else:
        extent_code = "_cam.isFitView = True"

    section_code = f"""
import adsk.core, adsk.fusion

# Use construction plane with offset
ref_plane = {plane_ref}
offset_val = adsk.core.ValueInput.createByReal({off})
plane_input = rootComp.constructionPlanes.createInput()
plane_input.setByOffset(ref_plane, offset_val)
section_plane = rootComp.constructionPlanes.add(plane_input)

# Set camera to look along the section axis
_vp = app.activeViewport
_cam = _vp.camera
_cam.isSmoothTransition = False

_target = {target_code}
axis = "{axis}"
if axis == "x":
    _cam.eye = adsk.core.Point3D.create(_target.x + {cam_dist}, _target.y, _target.z)
elif axis == "y":
    _cam.eye = adsk.core.Point3D.create(_target.x, _target.y + {cam_dist}, _target.z)
else:
    _cam.eye = adsk.core.Point3D.create(_target.x, _target.y, _target.z + {cam_dist})
_cam.target = _target
_cam.upVector = adsk.core.Vector3D.create(0, 0, 1)
{extent_code}
_vp.camera = _cam
if {view_extent is None}:
    _vp.fit()
adsk.doEvents()

print(f"Section plane created on {{axis.upper()}} axis at offset {{{off}*10:.1f}}mm")
focus_info = "{f'Focus: [{focus_point[0]}, {focus_point[1]}, {focus_point[2]}] mm' if focus_point else 'Auto-fit view'}"
print(focus_info)
"""
    result = await send_to_fusion(section_code, render=True)
    return _build_response(result, read_image_as_base64)


async def _handle_api_docs(arguments, send_to_fusion, read_image_as_base64):
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
    _search_lower = _search.lower()
    for _mod in _MODULES:
        _mod_prefix = _mod.__name__.split(".")[-1]
        for _name in sorted(dir(_mod)):
            if _name.startswith('_'):
                continue
            _cls = getattr(_mod, _name, None)
            if _cls is None or not isinstance(_cls, type):
                continue
            if _search_lower in _name.lower():
                _doc = (_cls.__doc__ or "").split("\\n")[0][:120]
                _results.append(f"[class] {{_mod_prefix}}.{{_name}}  — {{_doc}}")
                continue
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
                    break
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
    result = await send_to_fusion(docs_code, render=False)
    return _build_response(result, read_image_as_base64)


async def _handle_mesh_analyze(arguments, send_to_fusion, read_image_as_base64):
    stl_path = arguments.get("stl_path", "")
    body_name = arguments.get("body_name", "")
    detect_features = arguments.get("detect_features", True)
    sample_size = arguments.get("sample_size", 5000)
    min_radius = arguments.get("min_radius", 5)
    min_circularity = arguments.get("min_circularity", 0.5)

    if stl_path:
        return _mesh_analyze_stl(
            stl_path, detect_features, sample_size, min_radius, min_circularity
        )
    else:
        result = await send_to_fusion(
            _mesh_analyze_fusion_code(body_name, detect_features, sample_size, min_radius, min_circularity),
            render=True, timeout=60,
        )
        return _build_response(result, read_image_as_base64)


def _handle_mesh_modify(arguments):
    stl_input = arguments["stl_input"]
    stl_output = arguments.get("stl_output", str(Path.home() / "Desktop" / "modified.stl"))
    operation = arguments["operation"]
    axis = arguments.get("axis", "X")
    tolerance = arguments.get("tolerance", 0.05)
    center = arguments.get("center", [0, 0])
    current_radius = arguments.get("current_radius", 0)
    target_radius = arguments.get("target_radius", 0)

    try:
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

        vert_map: dict[tuple, list] = {}
        for tri in triangles:
            for v in [tri[1], tri[2], tri[3]]:
                key = (round(v[0], 6), round(v[1], 6), round(v[2], 6))
                if key not in vert_map:
                    vert_map[key] = []
                vert_map[key].append(v)

        axis_map = {
            "X": (1, 2),
            "Y": (0, 2),
            "Z": (0, 1),
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

        elif operation == "planar_shift":
            coordinate_value = arguments["coordinate_value"]
            target_value_op = arguments["target_value"]
            axis_idx = {"X": 0, "Y": 1, "Z": 2}[axis]
            modified = 0
            for (vx, vy, vz), vrefs in vert_map.items():
                coords = [vx, vy, vz]
                if abs(coords[axis_idx] - coordinate_value) < tolerance:
                    for vref in vrefs:
                        vref[axis_idx] = target_value_op
                    modified += 1

        with open(stl_output, "wb") as f:
            if operation == "radial_displacement":
                out_header = f"Modified: {operation} r={current_radius}->{target_radius}".encode()
            else:
                cv = arguments.get("coordinate_value", 0)
                tv = arguments.get("target_value", 0)
                out_header = f"Modified: {operation} {axis}={cv}->{tv}".encode()
            f.write(out_header.ljust(80, b"\0"))
            f.write(struct.pack("<I", n_tris))
            for normal, v0, v1, v2, attr in triangles:
                f.write(struct.pack("<fff", *normal))
                f.write(struct.pack("<fff", *v0))
                f.write(struct.pack("<fff", *v1))
                f.write(struct.pack("<fff", *v2))
                f.write(struct.pack("<H", attr))

        out_size = os.path.getsize(stl_output) / (1024 * 1024)
        if operation == "radial_displacement":
            op_detail = f"axis={axis}, center={center}, r={current_radius} -> {target_radius}"
        else:
            cv = arguments.get("coordinate_value", 0)
            tv = arguments.get("target_value", 0)
            op_detail = f"axis={axis}, {cv} -> {tv}"
        text = (
            f"Modified {modified} unique vertex positions\n"
            f"Operation: {operation} ({op_detail}, tolerance={tolerance})\n"
            f"Input: {stl_input} ({n_tris} triangles, {len(vert_map)} unique vertices)\n"
            f"Output: {stl_output} ({out_size:.1f} MB)"
        )
        return [TextContent(type="text", text=text)]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]


async def _handle_highlight(arguments, send_to_fusion, read_image_as_base64):
    position = arguments["position"]
    radius = arguments.get("radius", 2)
    label = arguments.get("label", "")
    clear = arguments.get("clear", False)

    px, py, pz = position[0], position[1], position[2]
    label_str = label or f"({px:.1f}, {py:.1f}, {pz:.1f})"

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
    result = await send_to_fusion(highlight_code, render=True)
    return _build_response(result, read_image_as_base64)


async def _handle_import_mesh(arguments, send_to_fusion, read_image_as_base64):
    stl_path = os.path.expanduser(arguments["stl_path"])
    body_name = arguments.get("name", "")
    scale = arguments.get("scale", 1.0)

    if not body_name:
        body_name = os.path.splitext(os.path.basename(stl_path))[0]

    if not os.path.exists(stl_path):
        return [TextContent(type="text", text=f"File not found: {stl_path}")]

    with open(stl_path, "rb") as f:
        _header = f.read(80)
        num_triangles = struct.unpack("<I", f.read(4))[0]

        vertex_map = {}
        coords = []
        normals = []
        indices = []
        vertex_idx = 0

        for _ in range(num_triangles):
            data = struct.unpack("<12fH", f.read(50))
            nx, ny, nz = data[0], data[1], data[2]

            for v in range(3):
                vx = data[3 + v * 3]
                vy = data[4 + v * 3]
                vz = data[5 + v * 3]

                key = (round(vx, 5), round(vy, 5), round(vz, 5))
                if key not in vertex_map:
                    vertex_map[key] = vertex_idx
                    coords.extend([vx * scale / 10.0, vy * scale / 10.0, vz * scale / 10.0])
                    normals.extend([nx, ny, nz])
                    vertex_idx += 1
                indices.append(vertex_map[key])

    import_code = f"""
import adsk.core, adsk.fusion

coords = {coords}
indices = {indices}
normals = {normals}

meshBody = rootComp.meshBodies.addByTriangleMeshData(coords, indices, normals, [])
meshBody.name = "{body_name}"

viewport = app.activeViewport
viewport.fit()

print(f"Imported: {{meshBody.name}}")
print(f"  Vertices: {vertex_idx:,}")
print(f"  Triangles: {num_triangles:,}")
print(f"  Mesh bodies total: {{rootComp.meshBodies.count}}")
"""
    result = await send_to_fusion(import_code, render=True, timeout=120)
    return _build_response(result, read_image_as_base64)


def _hot_reload_module(name):
    """Hot-reload a module from the server directory."""
    import importlib
    mod_path = os.path.join(os.path.dirname(__file__), f'{name}.py')
    spec = importlib.util.spec_from_file_location(name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _handle_open_file(arguments, send_to_fusion, read_image_as_base64):
    file_path = arguments["file_path"]
    close_others = arguments.get("close_others", True)

    if not os.path.exists(file_path):
        return [TextContent(type="text", text=f"File not found: {file_path}")]

    # Import first, then close old docs (closing before import breaks addin context)
    close_code = ""
    if close_others:
        close_code = """
# Close old documents (keep the newly opened one)
_closed = 0
while app.documents.count > 1:
    _found = False
    for _di in range(app.documents.count):
        _d2 = app.documents.item(_di)
        if _d2 != _doc:
            try:
                _d2.close(False)
                _closed += 1
                _found = True
                break
            except:
                pass
    if not _found:
        break
if _closed:
    print(f"Closed {_closed} old documents")
"""

    code = f'''
import os

_path = r"{file_path}"
_im = app.importManager
_opts = _im.createFusionArchiveImportOptions(_path)
_doc = _im.importToNewDocument(_opts)
if _doc:
    _d = adsk.fusion.Design.cast(_doc.products.itemByProductType('DesignProductType'))
    _tl = _d.timeline.count if _d else 0
    _bodies = 0
    if _d:
        _rc = _d.rootComponent
        _bodies = _rc.bRepBodies.count
        for _oi in range(_rc.allOccurrences.count):
            _bodies += _rc.allOccurrences.item(_oi).component.bRepBodies.count
    print(f"Opened: {{os.path.basename(_path)}}")
    print(f"Timeline: {{_tl}} steps")
    print(f"Bodies: {{_bodies}}")
    if _d:
        print(f"Type: {{'Parametric' if _d.designType == adsk.fusion.DesignTypes.ParametricDesignType else 'Direct'}}")
    {close_code}
else:
    print("ERROR: Failed to open file")
'''
    result = await send_to_fusion(code, timeout=60)
    return _build_response(result, read_image_as_base64)


async def _handle_init_reconstruction(send_to_fusion, read_image_as_base64):
    pipeline = _hot_reload_module('pipeline')
    pipeline.ensure_dirs()
    # Clean stale context and step codes from previous runs
    import glob
    ctx_file = pipeline.CONTEXT_FILE
    if ctx_file.exists():
        ctx_file.unlink()
    for f in pipeline.RECONSTRUCTION_DIR.glob("step_*_code.py"):
        f.unlink()
    code = pipeline.get_init_reconstruction_code()
    result = await send_to_fusion(code, render=True)
    return _build_response(result, read_image_as_base64)


async def _handle_switch_document(arguments, send_to_fusion, read_image_as_base64):
    pipeline = _hot_reload_module('pipeline')
    role = arguments["role"]
    code = pipeline.get_switch_document_code(role)
    result = await send_to_fusion(code, render=True)
    return _build_response(result, read_image_as_base64)


async def _handle_reconstruct_step(arguments, send_to_fusion, read_image_as_base64):
    # Safety: ensure we're on reconstruction document
    ok, msg = await _ensure_document_role("reconstruction", send_to_fusion)
    if not ok:
        return [TextContent(type="text", text=f"Document switch failed: {msg}")]
    if msg:
        pass  # auto-switched document

    pipeline = _hot_reload_module('pipeline')
    generator = _hot_reload_module('generator')

    step_index = arguments["step_index"]

    # Load step snapshot (construction data from design_to_python export)
    # For now, load from the JSON export if available
    snapshot_path = pipeline.SNAPSHOTS_DIR / f"step_{step_index}.json"

    # Load all steps for context (needed by generator)
    all_steps = []
    total = pipeline.get_step_count()
    for i in range(total):
        s = pipeline.load_step_snapshot(i)
        if s:
            all_steps.append(s)

    # Load design export (needed for context rebuild and step data)
    export_json_path = os.path.expanduser("~/Desktop/design_export.py.tmp.json")
    if not os.path.exists(export_json_path):
        return [TextContent(type="text", text=(
            f"No design export found at {export_json_path}. "
            "Run design_to_python first to export the original design's construction data."
        ))]

    with open(export_json_path, 'r') as f:
        export_data = json.load(f)

    timeline = export_data.get('timeline', [])
    if step_index >= len(timeline):
        return [TextContent(type="text", text=f"Step {step_index} out of range (timeline has {len(timeline)} steps)")]

    # Load or rebuild reconstruction context
    ctx_dict = pipeline.load_context()
    if ctx_dict:
        ctx = generator.ReconstructionContext.from_dict(ctx_dict)
    else:
        # Rebuild context by running through all previous steps (code gen only, no execution)
        ctx = generator.ReconstructionContext()
        for i in range(step_index):
            generator.generate_single_step(timeline[i], ctx, timeline)

    step = timeline[step_index]

    # Generate code for this step
    code = generator.generate_single_step(step, ctx, timeline)

    # For step 0, prepend user parameter creation
    if step_index == 0:
        params = export_data.get('parameters', [])
        if params:
            param_code = generator._generate_params_code(params)
            marker = '# ── Step'
            pos = code.find(marker)
            if pos > 0:
                code = code[:pos] + param_code + '\n' + code[pos:]

    # Execute in Fusion (no render — verify_step will render on FAIL if needed)
    result = await send_to_fusion(code, render=False, timeout=60)

    # If successful, save code and context
    output = result.get("output", "")
    error = result.get("error")

    if not error:
        pipeline.save_step_code(step_index, code)
        pipeline.save_context(ctx.to_dict())

    # Build response
    contents = []
    parts = []
    if output:
        parts.append(f"Output:\n{output}")
    if error:
        parts.append(f"Error:\n{error}")
    if not parts:
        parts.append(f"Step {step_index} executed (no output).")
    contents.append(TextContent(type="text", text="\n\n".join(parts)))

    # Only render on ERROR for debugging (saves ~50-100k tokens per successful step)
    if error:
        err_render = await send_to_fusion("pass", render=True, timeout=10)
        if err_render.get("render_path"):
            img_data = read_image_as_base64(err_render["render_path"])
            if img_data:
                contents.append(ImageContent(type="image", data=img_data, mimeType="image/png"))
                try:
                    os.unlink(err_render["render_path"])
                except OSError:
                    pass

    return contents


async def _handle_regression_test(arguments, send_to_fusion, read_image_as_base64):
    pipeline = _hot_reload_module('pipeline')
    verifier = _hot_reload_module('verifier')
    generator = _hot_reload_module('generator')

    up_to_step = arguments.get("up_to_step")
    regenerate = arguments.get("regenerate", False)

    if regenerate:
        # Regenerate mode: use current generator to create fresh code for each step
        import json
        export_path = pipeline.EXCHANGE_DIR / "design_export.json"
        if not export_path.exists():
            export_path = Path.home() / "Desktop" / "design_export.py.tmp.json"
        with open(str(export_path)) as f:
            export_data = json.load(f)
        timeline = export_data.get("timeline", [])
        max_step = len(timeline) - 1
        if up_to_step is not None:
            max_step = min(max_step, up_to_step)
        completed = list(range(max_step + 1))
    else:
        completed = pipeline.get_completed_steps()
        if not completed:
            return [TextContent(type="text", text="No saved step codes found. Run reconstruct_step first or use regenerate=true.")]
        if up_to_step is not None:
            completed = [s for s in completed if s <= up_to_step]

    # 1. Clear design
    clear_code = """
timeline = design.timeline
_deleted = 0
for i in range(timeline.count - 1, -1, -1):
    try:
        timeline.item(i).entity.deleteMe()
        _deleted += 1
    except:
        pass
for i in range(rootComp.occurrences.count - 1, -1, -1):
    try: rootComp.occurrences.item(i).deleteMe()
    except: pass
for i in range(rootComp.bRepBodies.count - 1, -1, -1):
    try: rootComp.bRepBodies.item(i).deleteMe()
    except: pass
for i in range(rootComp.sketches.count - 1, -1, -1):
    try: rootComp.sketches.item(i).deleteMe()
    except: pass
for i in range(rootComp.constructionPlanes.count - 1, -1, -1):
    try: rootComp.constructionPlanes.item(i).deleteMe()
    except: pass
print(f"Cleared {_deleted} items. Remaining: {design.timeline.count}")
"""
    await send_to_fusion(clear_code, render=False)

    # 2. Execute each step and verify
    results = []
    ctx = generator.ReconstructionContext() if regenerate else None

    for step_idx in completed:
        if regenerate:
            # Generate fresh code using current generator
            step = timeline[step_idx]
            code = generator.generate_single_step(step, ctx, timeline)
            # Save the fresh code
            pipeline.save_step_code(step_idx, code)
        else:
            code = pipeline.load_step_code(step_idx)
            if not code:
                results.append(f"Step {step_idx}: SKIP (no saved code)")
                continue

        # Execute
        exec_result = await send_to_fusion(code, render=False, timeout=60)
        if exec_result.get("error"):
            results.append(f"Step {step_idx}: EXEC_ERROR — {exec_result['error'][:100]}")
            continue

        # Verify
        verify_code = verifier.get_current_state_code()
        verify_result = await send_to_fusion(verify_code, render=False)
        current_state = verifier.parse_current_state_from_output(verify_result.get("output", ""))

        if current_state is None:
            results.append(f"Step {step_idx}: VERIFY_ERROR — couldn't extract state")
            continue

        verification = verifier.verify_step(step_idx, current_state)
        results.append(verification["summary"])

    summary = f"Regression test: {len(completed)} steps\n\n" + "\n".join(results)
    passed = sum(1 for r in results if "PASS" in r)
    summary += f"\n\n{'='*40}\nResult: {passed}/{len(completed)} passed"

    return [TextContent(type="text", text=summary)]


async def _handle_export_step_snapshots(arguments, send_to_fusion, read_image_as_base64):
    verifier = _hot_reload_module('verifier')
    max_steps = arguments.get("max_steps", 0)
    code = verifier.get_full_export_code(max_steps)
    result = await send_to_fusion(code, render=False, timeout=300)
    return _build_response(result, read_image_as_base64)


async def _handle_verify_step(arguments, send_to_fusion, read_image_as_base64):
    # Safety: ensure we're on reconstruction document
    ok, msg = await _ensure_document_role("reconstruction", send_to_fusion)
    if not ok:
        return [TextContent(type="text", text=f"Document switch failed: {msg}")]
    if msg:
        pass  # auto-switched document

    verifier = _hot_reload_module('verifier')

    step_index = arguments["step_index"]

    # Extract current state from Fusion (no render yet — save tokens on PASS)
    code = verifier.get_current_state_code()
    result = await send_to_fusion(code, render=False)

    output = result.get("output", "")
    current_state = verifier.parse_current_state_from_output(output)

    if current_state is None:
        contents = [TextContent(type="text", text=f"Failed to extract current state from Fusion.\nOutput: {output}")]
        if result.get("error"):
            contents[0] = TextContent(type="text", text=f"Failed to extract current state.\nError: {result['error']}\nOutput: {output}")
        return contents

    # Compare against ground truth
    verification = verifier.verify_step(step_index, current_state)

    contents = [TextContent(type="text", text=verification["summary"])]

    # Only render on FAIL for visual debugging (saves ~50-100k tokens per PASS)
    if verification.get("status") != "PASS":
        fail_render = await send_to_fusion("pass", render=True, timeout=10)
        if fail_render.get("render_path"):
            img_data = read_image_as_base64(fail_render["render_path"])
            if img_data:
                contents.append(ImageContent(type="image", data=img_data, mimeType="image/png"))
                try:
                    os.unlink(fail_render["render_path"])
                except OSError:
                    pass

    return contents


async def _handle_design_to_python(arguments, send_to_fusion, read_image_as_base64):
    output_path = arguments.get("output_path", "~/Desktop/design_export.py")
    output_path = os.path.expanduser(output_path)

    # This is the large Fusion-side export script — loaded from a separate file
    # to keep this module manageable
    export_code = _get_design_export_code(output_path)

    result = await send_to_fusion(export_code, render=False, timeout=120)

    # Read the JSON data and generate reconstruction script
    tmp_path = output_path + ".tmp.json"
    try:
        with open(tmp_path, 'r') as f:
            design_data = json.load(f)

        script = _generate_reconstruction_script(design_data)
        with open(output_path, 'w') as f:
            f.write(f"# Fusion 360 Reconstruction Script (auto-generated by design_to_python)\n")
            f.write(f"# Timeline steps: {design_data.get('timeline_count', '?')}\n")
            f.write(f"# Parameters: {len(design_data.get('parameters', []))}\n\n")
            f.write(script)

        result_text = result.get("output", "") if isinstance(result, dict) else str(result)
        result_text += f"\nGenerated reconstruction script: {output_path}"
        if isinstance(result, dict):
            result['output'] = result_text
    except Exception as e:
        if isinstance(result, dict):
            result['output'] = result.get('output', '') + f"\nScript generation error: {e}. JSON saved to {tmp_path}"

    return _build_response(result, read_image_as_base64)


def _get_design_export_code(output_path: str) -> str:
    """Load the design export Fusion script from export_script.py if it exists,
    otherwise fall back to inline code."""
    export_script_path = os.path.join(os.path.dirname(__file__), 'export_script.py')
    if os.path.exists(export_script_path):
        with open(export_script_path, 'r') as f:
            template = f.read()
        return template.replace('__OUTPUT_PATH__', output_path)

    # Fallback: inline (will be migrated to export_script.py later)
    return _get_design_export_code_inline(output_path)


def _get_design_export_code_inline(output_path: str) -> str:
    """Inline version of the design export Fusion code — kept for backward compatibility."""
    return f'''
import adsk.core, adsk.fusion, json, math, traceback

output_path = "{output_path}"
timeline = design.timeline
tl_count = timeline.count

if design.designType != adsk.fusion.DesignTypes.ParametricDesignType:
    print("ERROR: Design is not parametric (no timeline)")
else:
    steps = []

    params = []
    for pi in range(design.userParameters.count):
        p = design.userParameters.item(pi)
        params.append({{"name": p.name, "expression": p.expression, "unit": p.unit,
                        "value": round(p.value, 6)}})

    def _describe_face(f):
        g = f.geometry
        gt = g.objectType.split("::")[-1] if g else "unknown"
        d = {{"type": gt}}
        try:
            _fbb = f.boundingBox
            d["bb_center"] = [round((_fbb.minPoint.x+_fbb.maxPoint.x)/2, 3),
                              round((_fbb.minPoint.y+_fbb.maxPoint.y)/2, 3),
                              round((_fbb.minPoint.z+_fbb.maxPoint.z)/2, 3)]
            if hasattr(g, 'normal'):
                d["normal"] = [round(g.normal.x,4), round(g.normal.y,4), round(g.normal.z,4)]
            elif hasattr(g, 'axis') and hasattr(g, 'radius'):
                d["axis"] = [round(g.axis.x,4), round(g.axis.y,4), round(g.axis.z,4)]
                d["radius"] = round(g.radius, 4)
        except:
            pass
        return d

    def _extract_edge_descriptors(feature, timeline, ti, tl_count, rootComp):
        """Shared: extract edge descriptors for fillet/chamfer via timeline rollback + plane intersection."""
        edge_descriptors = []
        try:
            _bbs = []
            _feature_tids = set()
            for _fi in range(feature.faces.count):
                _feature_tids.add(feature.faces.item(_fi).tempId)
            for _fi in range(feature.faces.count):
                _ff = feature.faces.item(_fi)
                _fbb = _ff.boundingBox
                _ex = (_fbb.minPoint.x + _fbb.maxPoint.x) / 2
                _ey = (_fbb.minPoint.y + _fbb.maxPoint.y) / 2
                _ez = (_fbb.minPoint.z + _fbb.maxPoint.z) / 2
                _dx = _fbb.maxPoint.x - _fbb.minPoint.x
                _dy = _fbb.maxPoint.y - _fbb.minPoint.y
                _dz = _fbb.maxPoint.z - _fbb.minPoint.z
                _max_ext = max(_dx, _dy, _dz)
                _px, _py, _pz = [], [], []
                _seen_adj = set()
                for _ei2 in range(_ff.edges.count):
                    _edge2 = _ff.edges.item(_ei2)
                    for _afi in range(_edge2.faces.count):
                        _af = _edge2.faces.item(_afi)
                        _atid = _af.tempId
                        if _atid not in _feature_tids and _atid not in _seen_adj:
                            _seen_adj.add(_atid)
                            try:
                                _ag = _af.geometry
                                if _ag.objectType.split("::")[-1] == "Plane":
                                    _an = _ag.normal
                                    _ao = _ag.origin
                                    if abs(_an.x) > 0.9 and _dx < _max_ext:
                                        _px.append(_ao.x)
                                    elif abs(_an.y) > 0.9 and _dy < _max_ext:
                                        _py.append(_ao.y)
                                    elif abs(_an.z) > 0.9 and _dz < _max_ext:
                                        _pz.append(_ao.z)
                            except:
                                pass
                if _px: _ex = max(_px, key=lambda v: abs(v - _ex))
                if _py: _ey = max(_py, key=lambda v: abs(v - _ey))
                if _pz: _ez = max(_pz, key=lambda v: abs(v - _ez))
                _bbs.append((_ex, _ey, _ez))
            _parent_comp = feature.parentComponent if feature.parentComponent else rootComp
            _body_name = None
            try:
                if feature.bodies.count > 0:
                    _body_name = feature.bodies.item(0).name
            except:
                pass
            timeline.markerPosition = ti
            _body = None
            for _bi in range(_parent_comp.bRepBodies.count):
                _b = _parent_comp.bRepBodies.item(_bi)
                if _body_name and _b.name == _body_name:
                    _body = _b
                    break
            if _body is None and _parent_comp.bRepBodies.count > 0:
                _body = _parent_comp.bRepBodies.item(0)
            if _body:
                _used = set()
                for (_bcx, _bcy, _bcz) in _bbs:
                    _best = None
                    _best_d = 1e9
                    _best_pt = None
                    _best_idx = -1
                    for _ei in range(_body.edges.count):
                        if _ei in _used:
                            continue
                        _e = _body.edges.item(_ei)
                        try:
                            _, _sp, _ep = _e.evaluator.getParameterExtents()
                            _, _mp = _e.evaluator.getPointAtParameter((_sp + _ep) / 2)
                            _d = ((_mp.x-_bcx)**2 + (_mp.y-_bcy)**2 + (_mp.z-_bcz)**2) ** 0.5
                            if _d < _best_d:
                                _best_d = _d
                                _best = _e
                                _best_pt = _mp
                                _best_idx = _ei
                        except:
                            continue
                    if _best and _best_d < 2.0:
                        _used.add(_best_idx)
                        _fa = _best.faces.item(0) if _best.faces.count > 0 else None
                        _fb = _best.faces.item(1) if _best.faces.count > 1 else None
                        edge_descriptors.append({{
                            "center": [round(_best_pt.x, 4), round(_best_pt.y, 4), round(_best_pt.z, 4)],
                            "face_a": _describe_face(_fa) if _fa else None,
                            "face_b": _describe_face(_fb) if _fb else None,
                        }})
            if ti + 1 < tl_count:
                timeline.markerPosition = ti + 1
            else:
                timeline.moveToEnd()
        except:
            try:
                if ti + 1 < tl_count:
                    timeline.markerPosition = ti + 1
                else:
                    timeline.moveToEnd()
            except:
                pass
        return edge_descriptors

    def _extract_feature_faces(feature):
        """Shared: extract face geometry info from a fillet/chamfer feature."""
        faces = []
        for fi in range(feature.faces.count):
            f = feature.faces.item(fi)
            bb = f.boundingBox
            gt = f.geometry.objectType.split("::")[-1] if f.geometry else "unknown"
            faces.append({{
                "geom_type": gt,
                "area": round(f.area, 6),
                "bb_min": [round(bb.minPoint.x,3), round(bb.minPoint.y,3), round(bb.minPoint.z,3)],
                "bb_max": [round(bb.maxPoint.x,3), round(bb.maxPoint.y,3), round(bb.maxPoint.z,3)],
            }})
        return faces

    def _get_feature_body_name(feature):
        """Shared: get body name from a feature."""
        try:
            if feature.bodies.count > 0:
                return feature.bodies.item(0).name
        except:
            pass
        return None

    for ti in range(tl_count):
        try:
            if ti + 1 < tl_count:
                timeline.markerPosition = ti + 1
            else:
                timeline.moveToEnd()
        except:
            pass

        item = timeline.item(ti)
        entity = item.entity
        etype = entity.objectType.split("::")[-1]
        step = {{"index": ti, "name": item.name, "type": etype}}
        try:
            if hasattr(entity, 'parentComponent') and entity.parentComponent:
                _pc = entity.parentComponent
                if _pc != rootComp:
                    step["parent_component"] = _pc.name
        except:
            pass

        try:
            if isinstance(entity, adsk.fusion.Sketch):
                sk = entity
                rp = sk.referencePlane
                try:
                    _st = sk.transform
                    step["plane_origin"] = [round(_st.translation.x,4), round(_st.translation.y,4), round(_st.translation.z,4)]
                    # Export sketch X-axis for face-based sketch orientation
                    step["sketch_x_axis"] = [round(_st.getCell(0,0),4), round(_st.getCell(1,0),4), round(_st.getCell(2,0),4)]
                except:
                    try:
                        if hasattr(rp, 'geometry') and hasattr(rp.geometry, 'origin'):
                            _org = rp.geometry.origin
                            step["plane_origin"] = [round(_org.x,4), round(_org.y,4), round(_org.z,4)]
                    except:
                        pass
                try:
                    if hasattr(rp, 'geometry') and hasattr(rp.geometry, 'normal'):
                        _pn = rp.geometry.normal
                        step["plane_normal"] = [round(_pn.x,4), round(_pn.y,4), round(_pn.z,4)]
                except:
                    pass

                if hasattr(rp, 'name'):
                    step["plane"] = rp.name
                elif hasattr(rp, 'objectType'):
                    ot = rp.objectType.split("::")[-1]
                    if ot == "BRepFace":
                        try:
                            g = rp.geometry
                            if hasattr(g, 'normal'):
                                n = g.normal
                                step["plane"] = "face"
                                step["face_normal"] = [round(n.x,3), round(n.y,3), round(n.z,3)]
                                bb = rp.boundingBox
                                step["face_center_z"] = round((bb.minPoint.z + bb.maxPoint.z)/2, 4)
                                step["face_area"] = round(rp.area, 4)
                        except:
                            step["plane"] = "BRepFace"
                    elif ot == "ConstructionPlane":
                        step["plane"] = rp.name
                    else:
                        step["plane"] = ot

                curves = []
                for ci in range(sk.sketchCurves.count):
                    c = sk.sketchCurves.item(ci)
                    ct = c.objectType.split("::")[-1]
                    cd = {{"type": ct, "construction": c.isConstruction}}
                    _CP = 8  # coordinate precision for sketch curves
                    if isinstance(c, adsk.fusion.SketchLine):
                        sp = c.startSketchPoint.geometry
                        ep = c.endSketchPoint.geometry
                        cd["start"] = [round(sp.x,_CP), round(sp.y,_CP)]
                        cd["end"] = [round(ep.x,_CP), round(ep.y,_CP)]
                    elif isinstance(c, adsk.fusion.SketchCircle):
                        cp = c.centerSketchPoint.geometry
                        cd["center"] = [round(cp.x,_CP), round(cp.y,_CP)]
                        cd["radius"] = round(c.radius, _CP)
                    elif isinstance(c, adsk.fusion.SketchArc):
                        cp = c.centerSketchPoint.geometry
                        sp = c.startSketchPoint.geometry
                        ep = c.endSketchPoint.geometry
                        cd["center"] = [round(cp.x,_CP), round(cp.y,_CP)]
                        cd["radius"] = round(c.radius, _CP)
                        cd["start"] = [round(sp.x,_CP), round(sp.y,_CP)]
                        cd["end"] = [round(ep.x,_CP), round(ep.y,_CP)]
                        # Export midpoint for sweep disambiguation (±π ambiguity)
                        try:
                            _geo = c.geometry
                            _ev = _geo.evaluator
                            _ok2, _ps, _pe = _ev.getParameterExtents()
                            if _ok2:
                                _ok3, _mp = _ev.getPointAtParameter((_ps+_pe)/2)
                                if _ok3:
                                    cd["mid"] = [round(_mp.x,_CP), round(_mp.y,_CP)]
                        except:
                            pass
                    curves.append(cd)
                step["curves"] = curves

            elif isinstance(entity, adsk.fusion.ExtrudeFeature):
                ext = entity
                step["operation"] = int(ext.operation)
                e1 = ext.extentOne
                step["extent_type"] = e1.objectType.split("::")[-1]
                if hasattr(e1, 'distance'):
                    step["distance"] = round(e1.distance.value, 4)
                    try:
                        step["distance_expr"] = e1.distance.expression
                    except: pass
                if ext.extentTwo and hasattr(ext.extentTwo, 'distance'):
                    step["distance2"] = round(ext.extentTwo.distance.value, 4)
                    try:
                        step["distance2_expr"] = ext.extentTwo.distance.expression
                    except: pass
                step["is_symmetric"] = hasattr(ext, 'isSymmetricExtent') and ext.isSymmetricExtent
                try:
                    _sk = ext.profile if not hasattr(ext.profile, 'count') else ext.profile.item(0)
                    _sk_z = _sk.parentSketch.origin.z
                    _body_bb = ext.bodies.item(0).boundingBox
                    _body_mid_z = (_body_bb.minPoint.z + _body_bb.maxPoint.z) / 2
                    step["body_z_min"] = round(_body_bb.minPoint.z, 4)
                    step["body_z_max"] = round(_body_bb.maxPoint.z, 4)
                except:
                    pass
                try:
                    profiles_info = []
                    prof = ext.profile
                    items = []
                    if hasattr(prof, 'objectType') and 'ObjectCollection' in prof.objectType:
                        for pi in range(prof.count):
                            items.append(prof.item(pi))
                    elif hasattr(prof, 'count') and not hasattr(prof, 'areaProperties'):
                        for pi in range(prof.count):
                            items.append(prof.item(pi))
                    else:
                        items.append(prof)
                    for p in items:
                        try:
                            ap = p.areaProperties()
                            profiles_info.append({{
                                "area": round(abs(ap.area), 4),
                                "centroid": [round(ap.centroid.x, 3), round(ap.centroid.y, 3)],
                            }})
                        except:
                            pass
                    step["profiles"] = profiles_info
                    try:
                        sketch = ext.profile if not items else items[0]
                        if hasattr(sketch, 'parentSketch'):
                            sk = sketch.parentSketch
                        elif hasattr(prof, 'parentSketch'):
                            sk = prof.parentSketch
                        else:
                            sk = None
                        if sk:
                            step["sketch_name"] = sk.name
                            profile_indices = []
                            for p in items:
                                matched = False
                                for spi in range(sk.profiles.count):
                                    if sk.profiles.item(spi) == p:
                                        profile_indices.append(spi)
                                        matched = True
                                        break
                                if not matched:
                                    try:
                                        pa = p.areaProperties()
                                        for spi in range(sk.profiles.count):
                                            spa = sk.profiles.item(spi).areaProperties()
                                            if abs(abs(spa.area) - abs(pa.area)) < 0.001 and \
                                               abs(spa.centroid.x - pa.centroid.x) < 0.01 and \
                                               abs(spa.centroid.y - pa.centroid.y) < 0.01:
                                                profile_indices.append(spi)
                                                break
                                    except:
                                        pass
                            step["profile_indices"] = profile_indices
                            try:
                                _rp = sk.referencePlane
                                if hasattr(_rp, 'geometry') and hasattr(_rp.geometry, 'normal'):
                                    _n = _rp.geometry.normal
                                    step["sketch_plane_normal"] = [round(_n.x,6), round(_n.y,6), round(_n.z,6)]
                            except:
                                pass
                    except:
                        pass
                except Exception as pe:
                    step["profiles_error"] = str(pe)
                try:
                    if ext.bodies.count > 0:
                        step["body_name"] = ext.bodies.item(0).name
                except:
                    pass
                try:
                    step["start_extent"] = ext.startExtent.objectType.split("::")[-1]
                except:
                    pass

            elif isinstance(entity, adsk.fusion.FilletFeature):
                fil = entity
                edge_sets = []
                for esi in range(fil.edgeSets.count):
                    es = fil.edgeSets.item(esi)
                    _es_data = {{"radius": round(es.radius.value, 4)}}
                    try:
                        _es_data["radius_expr"] = es.radius.expression
                    except: pass
                    edge_sets.append(_es_data)
                step["edge_sets"] = edge_sets
                step["edge_descriptors"] = _extract_edge_descriptors(fil, timeline, ti, tl_count, rootComp)
                step["faces"] = _extract_feature_faces(fil)
                _bn = _get_feature_body_name(fil)
                if _bn: step["body_name"] = _bn

            elif isinstance(entity, adsk.fusion.ChamferFeature):
                ch = entity
                edge_sets = []
                for esi in range(ch.edgeSets.count):
                    es = ch.edgeSets.item(esi)
                    _es_data = {{"distance": round(es.distance.value, 4)}}
                    try:
                        _es_data["distance_expr"] = es.distance.expression
                    except: pass
                    edge_sets.append(_es_data)
                step["edge_sets"] = edge_sets
                step["edge_descriptors"] = _extract_edge_descriptors(ch, timeline, ti, tl_count, rootComp)
                step["faces"] = _extract_feature_faces(ch)
                _bn = _get_feature_body_name(ch)
                if _bn: step["body_name"] = _bn

            elif isinstance(entity, adsk.fusion.RevolveFeature):
                rev = entity
                step["operation"] = int(rev.operation)
                try:
                    step["angle"] = round(math.degrees(rev.extentDefinition.angle.value), 1)
                    try:
                        step["angle_expr"] = rev.extentDefinition.angle.expression
                    except: pass
                except:
                    step["angle"] = 360.0
                try:
                    p = rev.profile
                    _items = [p] if hasattr(p, 'areaProperties') else [p.item(i) for i in range(p.count)]
                    if _items:
                        ap = _items[0].areaProperties()
                        step["profile"] = {{
                            "area": round(abs(ap.area), 4),
                            "centroid": [round(ap.centroid.x, 3), round(ap.centroid.y, 3)],
                        }}
                        if len(_items) > 1:
                            step["profiles"] = []
                            for _pi_item in _items:
                                _api = _pi_item.areaProperties()
                                step["profiles"].append({{
                                    "area": round(abs(_api.area), 4),
                                    "centroid": [round(_api.centroid.x, 3), round(_api.centroid.y, 3)],
                                }})
                        _sk = _items[0].parentSketch
                        if _sk:
                            step["sketch_name"] = _sk.name
                            profile_indices = []
                            for _p in _items:
                                for _spi in range(_sk.profiles.count):
                                    if _sk.profiles.item(_spi) == _p:
                                        profile_indices.append(_spi)
                                        break
                            step["profile_indices"] = profile_indices
                except:
                    pass
                try:
                    ax = rev.axis
                    if hasattr(ax, 'objectType') and 'SketchLine' in ax.objectType:
                        try:
                            _ask = ax.parentSketch
                            for _ci in range(_ask.sketchCurves.sketchLines.count):
                                if _ask.sketchCurves.sketchLines.item(_ci) == ax:
                                    step["axis_sketch_line_index"] = _ci
                                    step["axis_sketch_name"] = _ask.name
                                    break
                        except:
                            pass
                    if hasattr(ax, 'objectType') and 'BRepFace' in ax.objectType:
                        g = ax.geometry
                        if hasattr(g, 'axis'):
                            _a = g.axis
                            step["axis_direction"] = [round(_a.x,4), round(_a.y,4), round(_a.z,4)]
                            if hasattr(g, 'origin'):
                                step["axis_origin"] = [round(g.origin.x,4), round(g.origin.y,4), round(g.origin.z,4)]
                    elif hasattr(ax, 'startVertex'):
                        step["axis_start"] = [round(ax.startVertex.geometry.x,4),
                                              round(ax.startVertex.geometry.y,4),
                                              round(ax.startVertex.geometry.z,4)]
                        step["axis_end"] = [round(ax.endVertex.geometry.x,4),
                                            round(ax.endVertex.geometry.y,4),
                                            round(ax.endVertex.geometry.z,4)]
                        g = ax.geometry if hasattr(ax, 'geometry') else None
                        if g:
                            if hasattr(g, 'startPoint') and hasattr(g, 'endPoint'):
                                sp, ep = g.startPoint, g.endPoint
                                step["axis_origin"] = [round(sp.x,4), round(sp.y,4), round(sp.z,4)]
                                dx, dy, dz = ep.x-sp.x, ep.y-sp.y, ep.z-sp.z
                                ln = max((dx**2+dy**2+dz**2)**0.5, 1e-10)
                                step["axis_direction"] = [round(dx/ln,4), round(dy/ln,4), round(dz/ln,4)]
                    elif hasattr(ax, 'geometry'):
                        g = ax.geometry
                        if hasattr(g, 'origin') and hasattr(g, 'direction'):
                            step["axis_origin"] = [round(g.origin.x,4), round(g.origin.y,4), round(g.origin.z,4)]
                            step["axis_direction"] = [round(g.direction.x,4), round(g.direction.y,4), round(g.direction.z,4)]
                        elif hasattr(g, 'startPoint') and hasattr(g, 'endPoint'):
                            sp, ep = g.startPoint, g.endPoint
                            step["axis_origin"] = [round(sp.x,4), round(sp.y,4), round(sp.z,4)]
                            dx, dy, dz = ep.x-sp.x, ep.y-sp.y, ep.z-sp.z
                            ln = max((dx**2+dy**2+dz**2)**0.5, 1e-10)
                            step["axis_direction"] = [round(dx/ln,4), round(dy/ln,4), round(dz/ln,4)]
                except:
                    pass

            elif isinstance(entity, adsk.fusion.CircularPatternFeature):
                cp = entity
                try:
                    step["quantity"] = int(cp.quantity.value) if hasattr(cp.quantity, 'value') else int(cp.quantity)
                    try:
                        step["quantity_expr"] = cp.quantity.expression if hasattr(cp.quantity, 'expression') else None
                    except: pass
                except:
                    pass
                try:
                    step["total_angle"] = round(math.degrees(cp.totalAngle.value), 1)
                    try:
                        step["total_angle_expr"] = cp.totalAngle.expression
                    except: pass
                except:
                    step["total_angle"] = 360.0
                try:
                    ax = cp.axis
                    step["axis_type"] = ax.objectType.split("::")[-1]
                    if hasattr(ax, 'geometry'):
                        g = ax.geometry
                        if hasattr(g, 'origin') and hasattr(g, 'direction'):
                            step["axis_direction"] = [round(g.direction.x,4), round(g.direction.y,4), round(g.direction.z,4)]
                            step["axis_origin"] = [round(g.origin.x,4), round(g.origin.y,4), round(g.origin.z,4)]
                        elif hasattr(g, 'axis'):
                            _a = g.axis
                            step["axis_direction"] = [round(_a.x,4), round(_a.y,4), round(_a.z,4)]
                            if hasattr(g, 'origin'):
                                step["axis_origin"] = [round(g.origin.x,4), round(g.origin.y,4), round(g.origin.z,4)]
                        elif hasattr(g, 'startPoint') and hasattr(g, 'endPoint'):
                            sp, ep = g.startPoint, g.endPoint
                            step["axis_origin"] = [round(sp.x,4), round(sp.y,4), round(sp.z,4)]
                            dx, dy, dz = ep.x-sp.x, ep.y-sp.y, ep.z-sp.z
                            ln = max((dx**2+dy**2+dz**2)**0.5, 1e-10)
                            step["axis_direction"] = [round(dx/ln,4), round(dy/ln,4), round(dz/ln,4)]
                except:
                    pass
                try:
                    pat_faces = []
                    for fi in range(min(cp.faces.count, 20)):
                        f = cp.faces.item(fi)
                        bb = f.boundingBox
                        pat_faces.append({{
                            "bb_min": [round(bb.minPoint.x,3), round(bb.minPoint.y,3), round(bb.minPoint.z,3)],
                            "bb_max": [round(bb.maxPoint.x,3), round(bb.maxPoint.y,3), round(bb.maxPoint.z,3)],
                        }})
                    step["faces"] = pat_faces
                except:
                    pass
                try:
                    if cp.bodies.count > 0:
                        step["body_name"] = cp.bodies.item(0).name
                except:
                    pass

            elif isinstance(entity, adsk.fusion.ShellFeature):
                sh = entity
                try:
                    step["inside_thickness"] = round(sh.insideThickness.value, 6)
                    try:
                        step["inside_thickness_expr"] = sh.insideThickness.expression
                    except: pass
                except: pass
                try:
                    if sh.outsideThickness:
                        step["outside_thickness"] = round(sh.outsideThickness.value, 6)
                except: pass
                step["tangent_chain"] = sh.isTangentChain
                # Capture faces of the body BEFORE shell to identify removed face(s)
                # Roll back to before shell, describe all faces, then restore
                try:
                    _body_name = sh.bodies.item(0).name if sh.bodies.count > 0 else None
                    step["body_name"] = _body_name
                    timeline.markerPosition = ti
                    _pre_body = None
                    for _bi in range(rootComp.bRepBodies.count):
                        if rootComp.bRepBodies.item(_bi).name == _body_name:
                            _pre_body = rootComp.bRepBodies.item(_bi)
                            break
                    if _pre_body is None and rootComp.bRepBodies.count > 0:
                        _pre_body = rootComp.bRepBodies.item(0)
                    if _pre_body:
                        _all_faces = []
                        for _fi in range(_pre_body.faces.count):
                            _f = _pre_body.faces.item(_fi)
                            _g = _f.geometry
                            _gt = _g.objectType.split("::")[-1]
                            _bb = _f.boundingBox
                            _fd = {{"type": _gt, "area": round(_f.area, 4),
                                   "bb_center": [round((_bb.minPoint.x+_bb.maxPoint.x)/2, 3),
                                                 round((_bb.minPoint.y+_bb.maxPoint.y)/2, 3),
                                                 round((_bb.minPoint.z+_bb.maxPoint.z)/2, 3)]}}
                            if hasattr(_g, 'normal'):
                                _fd["normal"] = [round(_g.normal.x,3), round(_g.normal.y,3), round(_g.normal.z,3)]
                            _all_faces.append(_fd)
                        step["pre_faces"] = _all_faces
                    # Restore timeline
                    if ti + 1 < tl_count:
                        timeline.markerPosition = ti + 1
                    else:
                        timeline.moveToEnd()
                    # Determine removed faces by comparing pre/post face count and areas
                    _post_body = None
                    for _bi in range(rootComp.bRepBodies.count):
                        if rootComp.bRepBodies.item(_bi).name == _body_name:
                            _post_body = rootComp.bRepBodies.item(_bi)
                            break
                    if _post_body:
                        # For each pre-face, check if a similar face exists in post body
                        _removed = []
                        for _fd in _all_faces:
                            _found = False
                            for _fi in range(_post_body.faces.count):
                                _pf = _post_body.faces.item(_fi)
                                if abs(_pf.area - _fd["area"]) < max(_fd["area"] * 0.05, 0.01):
                                    _pbb = _pf.boundingBox
                                    _pc = [(_pbb.minPoint.x+_pbb.maxPoint.x)/2, (_pbb.minPoint.y+_pbb.maxPoint.y)/2, (_pbb.minPoint.z+_pbb.maxPoint.z)/2]
                                    _dc = _fd["bb_center"]
                                    if abs(_pc[0]-_dc[0])<0.2 and abs(_pc[1]-_dc[1])<0.2 and abs(_pc[2]-_dc[2])<0.2:
                                        _found = True
                                        break
                            if not _found:
                                _removed.append(_fd)
                        step["removed_faces"] = _removed
                except:
                    try:
                        if ti + 1 < tl_count:
                            timeline.markerPosition = ti + 1
                        else:
                            timeline.moveToEnd()
                    except: pass

            elif isinstance(entity, adsk.fusion.CombineFeature):
                cb = entity
                step["operation"] = int(cb.operation)
                try:
                    step["target_body"] = cb.targetBody.name
                except: pass
                try:
                    _tool_bodies = []
                    for _tbi in range(cb.toolBodies.count):
                        _tool_bodies.append(cb.toolBodies.item(_tbi).name)
                    step["tool_bodies"] = _tool_bodies
                except: pass
                step["is_keep_tools"] = cb.isKeepToolBodies if hasattr(cb, 'isKeepToolBodies') else False

            elif isinstance(entity, adsk.fusion.MirrorFeature):
                mf = entity
                try:
                    mp = mf.mirrorPlane
                    _mpt = mp.objectType.split("::")[-1]
                    step["mirror_plane_type"] = _mpt
                    if _mpt == "ConstructionPlane":
                        step["mirror_plane_name"] = mp.name
                    elif _mpt == "BRepFace":
                        _mg = mp.geometry
                        if hasattr(_mg, 'normal'):
                            step["mirror_plane_normal"] = [round(_mg.normal.x,3), round(_mg.normal.y,3), round(_mg.normal.z,3)]
                        _mbb = mp.boundingBox
                        step["mirror_plane_center"] = [round((_mbb.minPoint.x+_mbb.maxPoint.x)/2,3), round((_mbb.minPoint.y+_mbb.maxPoint.y)/2,3), round((_mbb.minPoint.z+_mbb.maxPoint.z)/2,3)]
                except: pass
                step["operation"] = int(mf.operation) if hasattr(mf, 'operation') else 0
                try:
                    if mf.bodies.count > 0:
                        step["body_name"] = mf.bodies.item(0).name
                except: pass
                # Input features/bodies
                try:
                    _input_tl = []
                    for _ii in range(mf.inputEntities.count):
                        _ie = mf.inputEntities.item(_ii)
                        if hasattr(_ie, 'timelineObject'):
                            _input_tl.append(_ie.timelineObject.index)
                    step["input_timeline_indices"] = _input_tl
                except: pass

            elif isinstance(entity, adsk.fusion.OffsetFacesFeature):
                of = entity
                try:
                    step["offset_distance"] = round(of.distance.value, 6)
                    try:
                        step["offset_distance_expr"] = of.distance.expression
                    except: pass
                except: pass
                try:
                    _of_faces = []
                    for _fi in range(of.faces.count):
                        _f = of.faces.item(_fi)
                        _bb = _f.boundingBox
                        _g = _f.geometry
                        _fd = {{"bb_center": [round((_bb.minPoint.x+_bb.maxPoint.x)/2,3), round((_bb.minPoint.y+_bb.maxPoint.y)/2,3), round((_bb.minPoint.z+_bb.maxPoint.z)/2,3)],
                               "area": round(_f.area, 4),
                               "type": _g.objectType.split("::")[-1] if _g else "unknown"}}
                        if hasattr(_g, 'normal'):
                            _fd["normal"] = [round(_g.normal.x,3), round(_g.normal.y,3), round(_g.normal.z,3)]
                        _of_faces.append(_fd)
                    step["faces"] = _of_faces
                except: pass
                try:
                    if of.bodies.count > 0:
                        step["body_name"] = of.bodies.item(0).name
                except: pass

            elif isinstance(entity, adsk.fusion.SplitBodyFeature):
                sb = entity
                try:
                    _st = sb.splittingTool
                    step["splitting_tool_type"] = _st.objectType.split("::")[-1]
                    if hasattr(_st, 'name'):
                        step["splitting_tool_name"] = _st.name
                    elif hasattr(_st, 'geometry'):
                        _sg = _st.geometry
                        if hasattr(_sg, 'normal'):
                            step["splitting_tool_normal"] = [round(_sg.normal.x,3), round(_sg.normal.y,3), round(_sg.normal.z,3)]
                        _sbb = _st.boundingBox
                        step["splitting_tool_center"] = [round((_sbb.minPoint.x+_sbb.maxPoint.x)/2,3), round((_sbb.minPoint.y+_sbb.maxPoint.y)/2,3), round((_sbb.minPoint.z+_sbb.maxPoint.z)/2,3)]
                except: pass
                try:
                    if sb.bodies.count > 0:
                        step["body_name"] = sb.bodies.item(0).name
                except: pass

            elif isinstance(entity, adsk.fusion.DraftFeature):
                df = entity
                try:
                    step["angle"] = round(math.degrees(df.angle.value), 4)
                except: pass
                try:
                    _df_faces = []
                    for _fi in range(df.inputFaces.count):
                        _f = df.inputFaces.item(_fi)
                        _bb = _f.boundingBox
                        _g = _f.geometry
                        _fd = {{"bb_center": [round((_bb.minPoint.x+_bb.maxPoint.x)/2,3), round((_bb.minPoint.y+_bb.maxPoint.y)/2,3), round((_bb.minPoint.z+_bb.maxPoint.z)/2,3)],
                               "area": round(_f.area, 4),
                               "type": _g.objectType.split("::")[-1] if _g else "unknown"}}
                        if hasattr(_g, 'normal'):
                            _fd["normal"] = [round(_g.normal.x,3), round(_g.normal.y,3), round(_g.normal.z,3)]
                        _of_faces.append(_fd)
                    step["input_faces"] = _df_faces
                except: pass
                try:
                    _pp = df.pullDirection
                    if _pp:
                        step["pull_direction_type"] = _pp.objectType.split("::")[-1]
                except: pass
                try:
                    if df.bodies.count > 0:
                        step["body_name"] = df.bodies.item(0).name
                except: pass

            elif isinstance(entity, adsk.fusion.RectangularPatternFeature):
                rp = entity
                try:
                    step["quantity_one"] = int(rp.quantityOne.value) if hasattr(rp.quantityOne, 'value') else int(rp.quantityOne)
                except: pass
                try:
                    step["distance_one"] = round(rp.distanceOne.value, 6)
                except: pass
                try:
                    step["quantity_two"] = int(rp.quantityTwo.value) if hasattr(rp.quantityTwo, 'value') else int(rp.quantityTwo)
                except: pass
                try:
                    step["distance_two"] = round(rp.distanceTwo.value, 6)
                except: pass
                try:
                    ax1 = rp.directionOneEntity
                    if hasattr(ax1, 'geometry'):
                        g = ax1.geometry
                        if hasattr(g, 'direction'):
                            step["direction_one"] = [round(g.direction.x,4), round(g.direction.y,4), round(g.direction.z,4)]
                        elif hasattr(g, 'axis'):
                            step["direction_one"] = [round(g.axis.x,4), round(g.axis.y,4), round(g.axis.z,4)]
                except: pass
                try:
                    ax2 = rp.directionTwoEntity
                    if ax2 and hasattr(ax2, 'geometry'):
                        g = ax2.geometry
                        if hasattr(g, 'direction'):
                            step["direction_two"] = [round(g.direction.x,4), round(g.direction.y,4), round(g.direction.z,4)]
                        elif hasattr(g, 'axis'):
                            step["direction_two"] = [round(g.axis.x,4), round(g.axis.y,4), round(g.axis.z,4)]
                except: pass
                # Input features
                try:
                    _input_tl = []
                    for _ii in range(rp.inputEntities.count):
                        _ie = rp.inputEntities.item(_ii)
                        if hasattr(_ie, 'timelineObject'):
                            _input_tl.append(_ie.timelineObject.index)
                    step["input_timeline_indices"] = _input_tl
                except: pass
                try:
                    if rp.bodies.count > 0:
                        step["body_name"] = rp.bodies.item(0).name
                except: pass

            elif isinstance(entity, adsk.fusion.ConstructionPlane):
                cp = entity
                defn = cp.definition
                step["defn_type"] = defn.objectType.split("::")[-1] if defn else "unknown"
                if hasattr(defn, 'offset'):
                    step["offset"] = round(defn.offset.value, 4)
                    try:
                        step["offset_expr"] = defn.offset.expression
                    except: pass
                try:
                    _cpg = cp.geometry
                    step["geometry_origin"] = [round(_cpg.origin.x,4), round(_cpg.origin.y,4), round(_cpg.origin.z,4)]
                    step["geometry_normal"] = [round(_cpg.normal.x,4), round(_cpg.normal.y,4), round(_cpg.normal.z,4)]
                except:
                    pass
                try:
                    parent = defn.planarEntity
                    if hasattr(parent, 'name'):
                        step["parent"] = parent.name
                    elif hasattr(parent, 'objectType'):
                        pt = parent.objectType.split("::")[-1]
                        if pt == "BRepFace":
                            n = parent.geometry.normal
                            step["parent"] = "face"
                            step["parent_normal"] = [round(n.x,3), round(n.y,3), round(n.z,3)]
                            step["parent_z"] = round((parent.boundingBox.minPoint.z + parent.boundingBox.maxPoint.z)/2, 3)
                        elif pt == "ConstructionPlane":
                            step["parent"] = parent.name
                        else:
                            step["parent"] = pt
                except:
                    pass

            elif isinstance(entity, adsk.fusion.Occurrence):
                step["component_name"] = entity.component.name

        except Exception as ex:
            step["error"] = str(ex)

        steps.append(step)

    try:
        timeline.moveToEnd()
    except:
        pass

    # Second pass: collect CircularPattern input via timeline groups
    for _s in steps:
        if _s.get("type") != "CircularPatternFeature":
            continue
        _pat_ti = _s["index"]
        try:
            _pat_item = timeline.item(_pat_ti)
            input_timeline_indices = []
            input_types = []
            if hasattr(_pat_item, 'parentGroup') and _pat_item.parentGroup:
                _grp = _pat_item.parentGroup
                for _gi in range(_grp.count):
                    _child = _grp.item(_gi)
                    if _child.index != _pat_ti:
                        try:
                            _ce = _child.entity
                            input_timeline_indices.append(_child.index)
                            input_types.append(_ce.objectType.split("::")[-1])
                        except:
                            input_timeline_indices.append(_child.index)
                            input_types.append("unknown")
            if not input_timeline_indices and hasattr(_pat_item, 'isGroup') and _pat_item.isGroup:
                for _gi in range(_pat_item.count):
                    _child = _pat_item.item(_gi)
                    try:
                        _ce = _child.entity
                        input_timeline_indices.append(_child.index)
                        input_types.append(_ce.objectType.split("::")[-1])
                    except:
                        input_timeline_indices.append(_child.index)
                        input_types.append("unknown")
            _s["input_timeline_indices"] = input_timeline_indices
            _s["input_types"] = input_types
        except Exception as _grperr:
            _s["_input_error"] = str(_grperr)

    # ── Collect visual properties (appearances, materials, opacity, visibility) ──
    visual = {{"bodies": [], "face_overrides": [], "occurrences": []}}
    try:
        # Collect from all components (root + sub-components)
        def _collect_bodies(comp, comp_path=""):
            for _bi in range(comp.bRepBodies.count):
                _b = comp.bRepBodies.item(_bi)
                _bv = {{"name": _b.name, "component": comp_path}}
                try:
                    _bv["volume"] = round(_b.physicalProperties.volume, 6)
                    _bbb = _b.boundingBox
                    _bv["bb_min"] = [round(_bbb.minPoint.x,3), round(_bbb.minPoint.y,3), round(_bbb.minPoint.z,3)]
                    _bv["bb_max"] = [round(_bbb.maxPoint.x,3), round(_bbb.maxPoint.y,3), round(_bbb.maxPoint.z,3)]
                except:
                    pass
                if _b.appearance:
                    _bv["appearance"] = _b.appearance.name
                    _bv["appearance_id"] = _b.appearance.id
                if _b.material:
                    _bv["material"] = _b.material.name
                    _bv["material_id"] = _b.material.id
                if _b.opacity < 1.0:
                    _bv["opacity"] = round(_b.opacity, 4)
                if not _b.isVisible:
                    _bv["visible"] = False
                visual["bodies"].append(_bv)
                # Per-face appearance overrides
                _body_app = _b.appearance.name if _b.appearance else None
                for _fi in range(_b.faces.count):
                    _f = _b.faces.item(_fi)
                    if _f.appearance and _f.appearance.name != _body_app:
                        _fbb = _f.boundingBox
                        _fg = _f.geometry
                        _fgt = _fg.objectType.split("::")[-1] if _fg else "unknown"
                        visual["face_overrides"].append({{
                            "body": _b.name,
                            "component": comp_path,
                            "appearance": _f.appearance.name,
                            "appearance_id": _f.appearance.id,
                            "face_type": _fgt,
                            "face_area": round(_f.area, 6),
                            "face_bb_center": [
                                round((_fbb.minPoint.x+_fbb.maxPoint.x)/2, 3),
                                round((_fbb.minPoint.y+_fbb.maxPoint.y)/2, 3),
                                round((_fbb.minPoint.z+_fbb.maxPoint.z)/2, 3)],
                        }})
        _collect_bodies(rootComp, "")
        for _oi in range(rootComp.occurrences.count):
            _occ = rootComp.occurrences.item(_oi)
            _comp = _occ.component
            _collect_bodies(_comp, _comp.name)
            _ov = {{"component": _comp.name, "visible": _occ.isLightBulbOn}}
            if _occ.appearance:
                _ov["appearance"] = _occ.appearance.name
                _ov["appearance_id"] = _occ.appearance.id
            visual["occurrences"].append(_ov)
    except Exception as _ve:
        visual["_error"] = str(_ve)

    export = {{"parameters": params, "timeline": steps, "timeline_count": tl_count, "visual": visual}}

    tmp_path = output_path + ".tmp.json"
    with open(tmp_path, "w") as f:
        f.write(json.dumps(export, ensure_ascii=False))

    print(f"Exported {{tl_count}} timeline steps")
    print(f"Parameters: {{len(params)}}")
    print(f"Steps with profiles: {{sum(1 for s in steps if 'profiles' in s)}}")
    edge_steps = [s for s in steps if 'edge_sets' in s]
    faces_steps = sum(1 for s in steps if 'faces' in s)
    errors = sum(1 for s in steps if 'error' in s)
    print(f"Steps with edge_sets: {{len(edge_steps)}} ({{faces_steps}} with face geometry, {{errors}} errors)")
    print(f"JSON_PATH:{{tmp_path}}")
'''


# ---------------------------------------------------------------------------
#  mesh_analyze — pure Python STL path
# ---------------------------------------------------------------------------

def _mesh_analyze_stl(
    stl_path: str,
    detect_features: bool,
    sample_size: int,
    min_radius: float,
    min_circularity: float,
) -> list[TextContent]:
    """Analyze an STL file using pure Python — no Fusion needed."""

    stl_path = os.path.expanduser(stl_path)
    with open(stl_path, "rb") as f:
        header = f.read(80)
        n_tris = struct.unpack("<I", f.read(4))[0]

        tri_normals = []
        tri_verts = []
        for _ in range(n_tris):
            nx, ny, nz = struct.unpack("<fff", f.read(12))
            v0 = struct.unpack("<fff", f.read(12))
            v1 = struct.unpack("<fff", f.read(12))
            v2 = struct.unpack("<fff", f.read(12))
            f.read(2)
            tri_normals.append((nx, ny, nz))
            tri_verts.append((v0, v1, v2))

    # Build indexed mesh
    vert_to_idx: dict[tuple, int] = {}
    coords: list[float] = []
    indices: list[int] = []

    for v0, v1, v2 in tri_verts:
        for v in (v0, v1, v2):
            key = (round(v[0], 5), round(v[1], 5), round(v[2], 5))
            if key not in vert_to_idx:
                vert_to_idx[key] = len(vert_to_idx)
                coords.extend(key)
            indices.append(vert_to_idx[key])

    n_verts = len(vert_to_idx)
    tri_count = len(tri_verts)

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

    # Surface segmentation
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

    features_json = []
    if detect_features:
        # Boundary edge detection
        edge_count: dict[tuple, int] = {}
        for ti in range(tri_count):
            i0, i1, i2 = indices[ti * 3], indices[ti * 3 + 1], indices[ti * 3 + 2]
            for a, b in ((i0, i1), (i1, i2), (i2, i0)):
                edge = (a, b) if a < b else (b, a)
                edge_count[edge] = edge_count.get(edge, 0) + 1

        boundary = [e for e, c in edge_count.items() if c == 1]
        lines.append(f"\n  Boundary edges: {len(boundary):,}")

        if boundary:
            # Contour extraction + hole detection (same as original)
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
                    cx_h, cy_h, cz_h = c["center_mm"]
                    if ci == 0:
                        centers_str += f"[{cx_h:.0f}, {cy_h:.0f}, {cz_h:.0f}]"
                    else:
                        centers_str += f" [{cx_h:.0f}, {cy_h:.0f}, {cz_h:.0f}]"
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

            # Curvature-based cylinder detection for watertight meshes
            axis_labels = [("Z", 0, 1, 2), ("X", 1, 2, 0), ("Y", 0, 2, 1)]
            perp_thresh = 0.15
            all_cylinders: list[dict] = []
            max_radius = min(bb_size) / 2.0

            for axis_name, u_idx, v_idx, ax_idx in axis_labels:
                ax_extent = bb_size[ax_idx]
                cyl_tris = []
                for si in range(0, tri_count, max(1, tri_count // min(tri_count, sample_size * 2))):
                    n = tri_normals[si]
                    if abs(n[ax_idx]) > perp_thresh:
                        continue
                    v0, v1, v2 = tri_verts[si]
                    cu_val = (v0[u_idx] + v1[u_idx] + v2[u_idx]) / 3
                    cv_val = (v0[v_idx] + v1[v_idx] + v2[v_idx]) / 3
                    cax = (v0[ax_idx] + v1[ax_idx] + v2[ax_idx]) / 3
                    nu = n[u_idx]
                    nv = n[v_idx]
                    nlen_val = math.sqrt(nu**2 + nv**2)
                    if nlen_val < 0.3:
                        continue
                    nu /= nlen_val
                    nv /= nlen_val
                    cyl_tris.append((cu_val, cv_val, cax, nu, nv))

                if len(cyl_tris) < 10:
                    continue

                import random
                random.seed(42)
                best_centers: list[tuple] = []
                n_samples = min(len(cyl_tris), 800)
                tried_centers: set[tuple] = set()

                for _ in range(n_samples):
                    i1 = random.randint(0, len(cyl_tris) - 1)
                    i2 = random.randint(0, len(cyl_tris) - 1)
                    if i1 == i2:
                        continue
                    p1 = cyl_tris[i1]
                    p2 = cyl_tris[i2]
                    u1, v1a, z1, nu1, nv1 = p1
                    u2, v2a, z2, nu2, nv2 = p2

                    cross = nu1 * nv2 - nv1 * nu2
                    if abs(cross) < 0.3:
                        continue

                    t1 = ((u2 - u1) * nv2 - (v2a - v1a) * nu2) / cross
                    est_cu = u1 + t1 * nu1
                    est_cv = v1a + t1 * nv1
                    r_est = abs(t1)

                    if r_est < min_radius or r_est > max_radius:
                        continue

                    r2_check = math.sqrt((u2 - est_cu)**2 + (v2a - est_cv)**2)
                    if abs(r2_check - r_est) > r_est * 0.15:
                        continue

                    qkey = (round(est_cu, 0), round(est_cv, 0), round(r_est, 0))
                    if qkey in tried_centers:
                        continue
                    tried_centers.add(qkey)

                    tol = max(r_est * 0.08, 0.1)
                    inliers = 0
                    z_vals = []
                    for px, py, pz, pnu, pnv in cyl_tris:
                        dist_val = math.sqrt((px - est_cu)**2 + (py - est_cv)**2)
                        if abs(dist_val - r_est) > tol:
                            continue
                        dx = px - est_cu
                        dy = py - est_cv
                        dlen = math.sqrt(dx**2 + dy**2)
                        if dlen < 0.01:
                            continue
                        dot = abs((dx / dlen) * pnu + (dy / dlen) * pnv)
                        if dot > 0.6:
                            inliers += 1
                            z_vals.append(pz)

                    if inliers >= 20 and z_vals:
                        z_vals.sort()
                        height = z_vals[-1] - z_vals[0]
                        if height <= ax_extent * 1.1 and height >= r_est * 0.3:
                            best_centers.append((est_cu, est_cv, r_est, inliers,
                                                 z_vals[0], z_vals[-1]))

                best_centers.sort(key=lambda c: -c[3])
                deduped_cyls = []
                for cu_val, cv_val, r, count, zmin, zmax in best_centers:
                    too_close = False
                    for di, (dcu, dcv, dr, dc, _, _) in enumerate(deduped_cyls):
                        dist_val = math.sqrt((cu_val - dcu)**2 + (cv_val - dcv)**2)
                        if dist_val < max(r, dr) * 0.7 and abs(r - dr) < max(r, dr) * 0.3:
                            too_close = True
                            break
                    if not too_close:
                        deduped_cyls.append((cu_val, cv_val, r, count, zmin, zmax))

                for cu_val, cv_val, r, count, zmin, zmax in deduped_cyls[:10]:
                    if r < min_radius:
                        continue
                    height = zmax - zmin
                    if axis_name == "Z":
                        center_3d = [round(cu_val, 2), round(cv_val, 2), round((zmin + zmax) / 2, 2)]
                    elif axis_name == "X":
                        center_3d = [round((zmin + zmax) / 2, 2), round(cu_val, 2), round(cv_val, 2)]
                    else:
                        center_3d = [round(cu_val, 2), round((zmin + zmax) / 2, 2), round(cv_val, 2)]

                    all_cylinders.append({
                        "axis": axis_name,
                        "center_mm": center_3d,
                        "radius_mm": round(r, 2),
                        "diameter_mm": round(r * 2, 2),
                        "height_mm": round(height, 1),
                        "inliers": count,
                        "z_range": [round(zmin, 1), round(zmax, 1)],
                    })

            if all_cylinders:
                all_cylinders.sort(key=lambda c: -(c["inliers"] / max(c["height_mm"], 0.1)))
                all_cylinders = [c for c in all_cylinders if c["radius_mm"] >= min_radius][:15]

                lines.append(f"\n  Detected {len(all_cylinders)} cylindrical features (curvature-based):\n")
                lines.append(f"  {'#':>3} | {'Axis':>4} | {'Diam mm':>8} | {'Radius':>7} | {'Height':>7} | {'Inliers':>7} | Center (mm)")
                lines.append("  " + "-" * 85)
                for ci, cyl in enumerate(all_cylinders):
                    cx_c, cy_c, cz_c = cyl["center_mm"]
                    lines.append(
                        f"  {ci+1:>3} | {cyl['axis']:>4} | {cyl['diameter_mm']:>8.1f} | "
                        f"{cyl['radius_mm']:>7.2f} | {cyl['height_mm']:>7.1f} | "
                        f"{cyl['inliers']:>7} | [{cx_c:.1f}, {cy_c:.1f}, {cz_c:.1f}]"
                    )

                for cyl in all_cylinders:
                    features_json.append({
                        "type": "cylinder",
                        "axis": cyl["axis"],
                        "center_mm": cyl["center_mm"],
                        "diameter_mm": cyl["diameter_mm"],
                        "radius_mm": cyl["radius_mm"],
                        "height_mm": cyl["height_mm"],
                        "z_range": cyl["z_range"],
                    })

    # Flat wall detection
    if detect_features:
        flat_thresh = 0.9
        flat_walls: list[dict] = []
        axis_names_flat = [("X", 0), ("Y", 1), ("Z", 2)]

        for axis_name, ax_idx in axis_names_flat:
            wall_coords: dict[float, list] = {}
            quant = max(bb_size[ax_idx] / 200, 0.05)

            for si in range(0, tri_count, max(1, tri_count // min(tri_count, sample_size * 2))):
                n = tri_normals[si]
                if abs(n[ax_idx]) < flat_thresh:
                    continue
                v0, v1, v2 = tri_verts[si]
                coord = (v0[ax_idx] + v1[ax_idx] + v2[ax_idx]) / 3
                e1 = (v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2])
                e2 = (v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2])
                cx_v = e1[1] * e2[2] - e1[2] * e2[1]
                cy_v = e1[2] * e2[0] - e1[0] * e2[2]
                cz_v = e1[0] * e2[1] - e1[1] * e2[0]
                area = 0.5 * math.sqrt(cx_v**2 + cy_v**2 + cz_v**2)

                qcoord = round(coord / quant) * quant
                centroid = [(v0[i] + v1[i] + v2[i]) / 3 for i in range(3)]
                wall_coords.setdefault(qcoord, []).append((area, centroid))

            sorted_coords = sorted(wall_coords.keys())
            clusters: list[tuple] = []
            i = 0
            while i < len(sorted_coords):
                cluster_coord = sorted_coords[i]
                total_area = 0.0
                all_centroids = []
                j = i
                while j < len(sorted_coords) and sorted_coords[j] - cluster_coord < quant * 3:
                    for area, cent in wall_coords[sorted_coords[j]]:
                        total_area += area
                        all_centroids.append(cent)
                    j += 1
                if total_area > 1.0 and len(all_centroids) >= 3:
                    other_axes = [k for k in range(3) if k != ax_idx]
                    bounds = {}
                    for oa in other_axes:
                        vals = [c[oa] for c in all_centroids]
                        bounds[oa] = (min(vals), max(vals))
                    avg_coord = sum(c[ax_idx] for c in all_centroids) / len(all_centroids)
                    clusters.append((avg_coord, total_area, bounds, len(all_centroids)))
                i = j

            for coord, area, bounds, count in clusters:
                if area < 2.0:
                    continue
                other_axes = [k for k in range(3) if k != ax_idx]
                spans = {k: bounds[k][1] - bounds[k][0] for k in other_axes}
                flat_walls.append({
                    "axis": axis_name,
                    "coordinate_mm": round(coord, 2),
                    "area_mm2": round(area, 1),
                    "bounds": {["X", "Y", "Z"][k]: [round(bounds[k][0], 1), round(bounds[k][1], 1)]
                               for k in other_axes},
                    "tri_count": count,
                })

        if flat_walls:
            flat_walls.sort(key=lambda w: -w["area_mm2"])
            flat_walls = flat_walls[:20]
            lines.append(f"\n  Flat walls ({len(flat_walls)}):\n")
            lines.append(f"  {'#':>3} | {'Axis':>4} | {'Coord mm':>9} | {'Area mm²':>9} | Bounds")
            lines.append("  " + "-" * 75)
            for wi, w in enumerate(flat_walls):
                bounds_str = ", ".join(f"{k}=[{v[0]:.1f}..{v[1]:.1f}]" for k, v in w["bounds"].items())
                lines.append(f"  {wi+1:>3} | {w['axis']:>4} | {w['coordinate_mm']:>9.2f} | {w['area_mm2']:>9.1f} | {bounds_str}")

            for w in flat_walls:
                features_json.append({"type": "flat_wall", **w})

    # Cross-section scan
    if detect_features:
        lines.append(f"\n  Cross-section scan (through-holes & channels):\n")
        axis_scan = [("X", 0, 1, 2), ("Y", 1, 0, 2), ("Z", 2, 0, 1)]

        for axis_name, ax_idx, u_idx, v_idx in axis_scan:
            ax_min, ax_max = bb_min[ax_idx], bb_max[ax_idx]
            ax_span = ax_max - ax_min
            if ax_span < 1.0:
                continue

            slice_tol = ax_span * 0.03
            channels_found = []

            for frac in [0.2, 0.5, 0.8]:
                slice_pos = ax_min + frac * ax_span

                slice_verts_uv = []
                for vi in range(n_verts):
                    vx = coords[vi * 3 + ax_idx]
                    if abs(vx - slice_pos) < slice_tol:
                        vu = coords[vi * 3 + u_idx]
                        vv = coords[vi * 3 + v_idx]
                        slice_verts_uv.append((vu, vv))

                if len(slice_verts_uv) < 5:
                    continue

                u_vals = [p[0] for p in slice_verts_uv]
                v_vals = [p[1] for p in slice_verts_uv]
                u_min_s, u_max_s = min(u_vals), max(u_vals)
                v_min_s, v_max_s = min(v_vals), max(v_vals)

                grid_res_scan = max((u_max_s - u_min_s), (v_max_s - v_min_s)) / 50
                if grid_res_scan < 0.2:
                    grid_res_scan = 0.2

                occupied_cells: set[tuple] = set()
                for vu, vv in slice_verts_uv:
                    gu = int((vu - u_min_s) / grid_res_scan)
                    gv = int((vv - v_min_s) / grid_res_scan)
                    occupied_cells.add((gu, gv))

                u_axis_name = ["X", "Y", "Z"][u_idx]
                v_axis_name = ["X", "Y", "Z"][v_idx]

                gv_min = int((v_min_s - v_min_s) / grid_res_scan)
                gv_max = int((v_max_s - v_min_s) / grid_res_scan)
                gu_min = int((u_min_s - u_min_s) / grid_res_scan)
                gu_max = int((u_max_s - u_min_s) / grid_res_scan)

                gaps_at_slice: list[dict] = []
                for gv in range(gv_min, gv_max + 1):
                    row_occupied = sorted([gu for gu in range(gu_min, gu_max + 1) if (gu, gv) in occupied_cells])
                    if len(row_occupied) < 2:
                        continue
                    for i_gap in range(len(row_occupied) - 1):
                        gap_start = row_occupied[i_gap]
                        gap_end = row_occupied[i_gap + 1]
                        gap_size = gap_end - gap_start - 1
                        if gap_size >= 2:
                            gap_u = u_min_s + (gap_start + gap_end) / 2 * grid_res_scan
                            gap_v = v_min_s + gv * grid_res_scan
                            gap_width = gap_size * grid_res_scan
                            gaps_at_slice.append({
                                "u": round(gap_u, 1), "v": round(gap_v, 1),
                                "width": round(gap_width, 1),
                            })

                if gaps_at_slice:
                    gap_clusters: list[list] = []
                    used_gaps: set[int] = set()
                    for gi, g in enumerate(gaps_at_slice):
                        if gi in used_gaps:
                            continue
                        cluster = [g]
                        used_gaps.add(gi)
                        for gj, g2 in enumerate(gaps_at_slice):
                            if gj in used_gaps:
                                continue
                            if abs(g["u"] - g2["u"]) < grid_res_scan * 4 and abs(g["v"] - g2["v"]) < grid_res_scan * 4:
                                cluster.append(g2)
                                used_gaps.add(gj)
                        gap_clusters.append(cluster)

                    for gc in gap_clusters:
                        if len(gc) < 2:
                            continue
                        avg_u = sum(g["u"] for g in gc) / len(gc)
                        avg_v = sum(g["v"] for g in gc) / len(gc)
                        avg_w = sum(g["width"] for g in gc) / len(gc)
                        v_spread = max(g["v"] for g in gc) - min(g["v"] for g in gc)
                        channels_found.append({
                            "slice_pos": round(slice_pos, 1),
                            "slice_frac": frac,
                            "center": {u_axis_name: round(avg_u, 1), v_axis_name: round(avg_v, 1)},
                            "width_mm": round(avg_w, 1),
                            "extent_mm": round(v_spread, 1),
                            "gap_count": len(gc),
                        })

            if channels_found:
                sig_channels = [ch for ch in channels_found
                                if ch["width_mm"] >= 1.5 and ch["gap_count"] >= 2]
                deduped_channels = []
                for ch in sig_channels:
                    merged = False
                    for dch in deduped_channels:
                        cv1 = list(ch["center"].values())
                        cv2 = list(dch["center"].values())
                        if (abs(cv1[0] - cv2[0]) < 3 and abs(cv1[1] - cv2[1]) < 3
                                and abs(ch["width_mm"] - dch["width_mm"]) < ch["width_mm"] * 0.5):
                            if ch["gap_count"] > dch["gap_count"]:
                                dch.update(ch)
                            merged = True
                            break
                    if not merged:
                        deduped_channels.append(ch)

                deduped_channels.sort(key=lambda c: -c["width_mm"])
                deduped_channels = deduped_channels[:10]

                if deduped_channels:
                    lines.append(f"  {axis_name}-axis slices:")
                    for ch in deduped_channels:
                        center_str = ", ".join(f"{k}={v}" for k, v in ch["center"].items())
                        lines.append(
                            f"    Slice {axis_name}={ch['slice_pos']:.1f} ({ch['slice_frac']:.0%}): "
                            f"channel at {center_str}, width~{ch['width_mm']:.1f}mm, "
                            f"extent~{ch['extent_mm']:.1f}mm ({ch['gap_count']} rows)"
                        )
                    for ch in deduped_channels:
                        features_json.append({"type": "channel", "axis": axis_name, **ch})

    # Feature relationships
    if detect_features and len(features_json) >= 2:
        cyl_features = [f for f in features_json if f.get("type") == "cylinder"]
        if len(cyl_features) >= 2:
            lines.append(f"\n  Feature relationships:\n")
            for i in range(len(cyl_features)):
                for j in range(i + 1, len(cyl_features)):
                    f1, f2 = cyl_features[i], cyl_features[j]
                    if f1.get("axis") != f2.get("axis"):
                        continue
                    c1, c2 = f1["center_mm"], f2["center_mm"]
                    dist_val = math.sqrt(sum((c1[k] - c2[k])**2 for k in range(3)))
                    gap = dist_val - f1["radius_mm"] - f2["radius_mm"]
                    lines.append(
                        f"    Cyl [{c1[0]:.1f},{c1[1]:.1f},{c1[2]:.1f}] r={f1['radius_mm']:.1f} ↔ "
                        f"Cyl [{c2[0]:.1f},{c2[1]:.1f},{c2[2]:.1f}] r={f2['radius_mm']:.1f}: "
                        f"dist={dist_val:.1f}mm, gap={gap:.1f}mm"
                    )

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
