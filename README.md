# Fusion MCP Bridge

MCP server that lets [Claude Code](https://claude.com/claude-code) execute Python scripts inside Autodesk Fusion 360 and see the result as a viewport render.

Unlike other Fusion 360 MCP servers that expose individual operations (draw box, fillet, etc.), this one gives Claude a single powerful tool — `execute_design` — which sends **arbitrary Python code** to Fusion 360 and returns the output + a screenshot. This makes it far more flexible and less fragile.

## How it works

```
Claude Code  ←→  MCP Server (stdio)  ←→  exchange/  ←→  Fusion 360 Add-in
                                         (JSON files)
```

- **MCP Server** writes `request.json` with Python code, polls for `response.json`
- **Fusion Add-in** polls for `request.json` via a background thread, executes the script on Fusion's main thread via `CustomEvent`, captures a viewport screenshot, writes `response.json`
- Communication is file-based — no HTTP, no sockets, no threading issues inside Fusion

## Tools

### Core

| Tool | Description |
|------|-------------|
| `execute_design` | Send Python code to Fusion 360, get output + viewport screenshot |
| `get_viewport` | Capture current viewport without executing code |
| `clear_design` | Remove all geometry from the active design |
| `inspect_design` | Inspect existing model: bodies, sketches, timeline, parameters + screenshot |

The `execute_design` script context has these globals: `app`, `ui`, `design`, `rootComp`, `adsk`.

### Mesh tools

| Tool | Description |
|------|-------------|
| `mesh_analyze` | Reverse-engineer mesh bodies (STL/OBJ): detect holes, circular features, surface segmentation. Returns compact summary instead of raw triangle data |
| `mesh_modify` | Modify STL files via pure Python (no Fusion needed). Supports `radial_displacement` to shrink/expand cylindrical holes |
| `highlight` | Place visual markers on the model to verify feature identification before modifying |
| `api_docs` | Search Fusion 360 API via live introspection — discover classes, methods, properties |

### Convenience

| Tool | Description |
|------|-------------|
| `undo` | Undo last N timeline operations, or rollback to a specific timeline index |
| `export_body` | Export a body as STL (high/medium/low refinement) for 3D printing |
| `measure` | Measure body dimensions, volume, area. Gap analysis between two bodies |
| `section_view` | Create a section plane on X/Y/Z axis at a given offset for inspecting internals |

#### Examples

```
# Undo last 3 operations
undo(count=3)

# Rollback to timeline index 26 (delete everything after)
undo(to_index=26)

# Export a body to Desktop as STL
export_body(body_name="Insert Top", refinement="high")

# Measure gap between frame and insert
measure(body_name="External Frame", body_name_2="Insert Bottom")

# Section view at Y=0 to see internal holes
section_view(axis="y", offset=0)

# Analyze a mesh body — find holes, features, surfaces
mesh_analyze(min_radius=2, min_circularity=0.7)

# Modify STL: shrink motor hole from r=3.1 to r=3.0
mesh_modify(
    stl_input="~/Desktop/car.stl",
    stl_output="~/Desktop/car_fixed.stl",
    operation="radial_displacement",
    axis="X",
    center=[40.8, 4.2],    # YZ center from mesh_analyze
    current_radius=3.1,
    target_radius=3.0,
    tolerance=0.05
)

# Place a marker to verify feature location
highlight(position=[30, 40.8, 4.2], radius=2, label="rear motor hole")
```

## Installation

### Prerequisites

- macOS or Windows
- Python 3.10+
- [Claude Code](https://claude.com/claude-code) installed
- Autodesk Fusion 360

### Quick install

```bash
git clone https://github.com/pegurov/fusion-mcp.git
cd fusion-mcp
./install.sh
```

### Manual install

1. **Create venv and install dependencies:**
   ```bash
   cd server
   python3 -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   pip install "mcp>=1.0.0"
   ```

2. **Register in Claude Code:**
   ```bash
   claude mcp add --scope user fusion-mcp-bridge -- \
     "$(pwd)/venv/bin/python" "$(pwd)/server.py"
   ```

3. **Install the Fusion 360 Add-in:**
   - Open Fusion 360
   - **UTILITIES** → **ADD-INS** (or `Shift+S`)
   - Click the green **+** → select the `addin/` folder
   - Select **FusionMCPBridge** → click **Run**
   - You should see: *"FusionMCPBridge started. Listening for scripts from Claude."*

4. **Restart Claude Code** to pick up the new MCP server.

## Usage

Just ask Claude to design something! Examples:

- *"Draw a box with rounded edges"*
- *"Create a phone stand"*
- *"Make a gear with 20 teeth"*

Claude will write Fusion 360 Python scripts, send them via the MCP, and see the rendered result to iterate on the design.

## Architecture

### Why file-based?

Fusion 360 runs a single-threaded event loop. Starting an HTTP server inside an add-in (like most other MCP bridges do) is fragile — it causes crashes, hangs, and race conditions.

File-based communication is dead simple and works with Fusion's `CustomEvent` pattern:
- A background thread polls `exchange/request.json` every 500ms
- When found, it fires a `CustomEvent` which Fusion processes on its main thread
- The script runs via `exec()` with full access to the Fusion API
- Results + viewport screenshot are written to `exchange/response.json`

### Camera

Before each render, the server appends camera setup code that:
- Sets isometric view (top-right)
- Calls `viewport.fit()` to frame all geometry
- Disables smooth transition for instant capture

## License

MIT
