"""
Verification system for step-by-step design reconstruction.

Provides:
- Ground truth capture from original design (per-step snapshots)
- Strict verification: exact match with no tolerances
- Comparison with detailed diff output
"""

import json
import os
from pathlib import Path

EXCHANGE_DIR = Path.home() / "fusion-mcp" / "exchange"
SNAPSHOTS_DIR = EXCHANGE_DIR / "snapshots"
RECONSTRUCTION_DIR = EXCHANGE_DIR / "reconstruction"

# Rounding precision — applied identically to ground truth and verification
VOLUME_PRECISION = 6    # decimal places for volume (cm3)
AREA_PRECISION = 6      # decimal places for surface area (cm2)
BBOX_PRECISION = 4      # decimal places for bbox coordinates (cm)


def ensure_dirs():
    """Create exchange subdirectories if they don't exist."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    RECONSTRUCTION_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
#  Ground truth extraction — Fusion-side code generators
# ---------------------------------------------------------------------------

def get_snapshot_extraction_code(step_index: int) -> str:
    """Return Fusion Python code that extracts ground truth metrics at a given timeline step.

    The script:
    1. Rolls timeline to step_index + 1
    2. Extracts body metrics (volume, area, bbox, face/edge count)
    3. Extracts sketch profile counts
    4. Prints JSON result
    """
    return f'''
import json

_step_index = {step_index}

# Roll timeline to this step
timeline = design.timeline
tl_count = timeline.count

try:
    if _step_index + 1 < tl_count:
        timeline.markerPosition = _step_index + 1
    else:
        timeline.moveToEnd()
except Exception as _te:
    print(f"TIMELINE_ERROR: {{_te}}")

# Extract body metrics
_bodies = []
for _bi in range(rootComp.bRepBodies.count):
    _b = rootComp.bRepBodies.item(_bi)
    _bb = _b.boundingBox
    try:
        _pp = _b.physicalProperties
        _vol = round(_pp.volume, {VOLUME_PRECISION})
        _area = round(_pp.area, {AREA_PRECISION})
    except:
        _vol = 0.0
        _area = 0.0
    _bodies.append({{
        "name": _b.name,
        "volume": _vol,
        "area": _area,
        "bbox_min": [round(_bb.minPoint.x, {BBOX_PRECISION}), round(_bb.minPoint.y, {BBOX_PRECISION}), round(_bb.minPoint.z, {BBOX_PRECISION})],
        "bbox_max": [round(_bb.maxPoint.x, {BBOX_PRECISION}), round(_bb.maxPoint.y, {BBOX_PRECISION}), round(_bb.maxPoint.z, {BBOX_PRECISION})],
        "faces": _b.faces.count,
        "edges": _b.edges.count,
    }})

# Extract sketch info (profile counts)
_sketches = []
for _si in range(rootComp.sketches.count):
    _sk = rootComp.sketches.item(_si)
    _sketches.append({{
        "name": _sk.name,
        "profiles": _sk.profiles.count,
        "curves": _sk.sketchCurves.count,
    }})

_result = {{
    "step_index": _step_index,
    "body_count": len(_bodies),
    "bodies": _bodies,
    "sketch_count": len(_sketches),
    "sketches": _sketches,
    "total_volume": round(sum(_b["volume"] for _b in _bodies), {VOLUME_PRECISION}),
    "total_area": round(sum(_b["area"] for _b in _bodies), {AREA_PRECISION}),
    "total_faces": sum(_b["faces"] for _b in _bodies),
    "total_edges": sum(_b["edges"] for _b in _bodies),
}}

print("GROUND_TRUTH_JSON:" + json.dumps(_result, ensure_ascii=False))
'''


def get_full_export_code(total_steps: int) -> str:
    """Return Fusion code that captures ground truth for ALL steps in one run.

    More efficient than calling get_snapshot_extraction_code per step,
    as it only needs one Fusion execution.
    """
    snapshots_dir = str(SNAPSHOTS_DIR)
    return f'''
import json, os

_snapshots_dir = r"{snapshots_dir}"
os.makedirs(_snapshots_dir, exist_ok=True)

timeline = design.timeline
tl_count = timeline.count
_total = min(tl_count, {total_steps}) if {total_steps} > 0 else tl_count

for _ti in range(_total):
    try:
        if _ti + 1 < tl_count:
            timeline.markerPosition = _ti + 1
        else:
            timeline.moveToEnd()
    except Exception as _te:
        print(f"Step {{_ti}}: timeline error - {{_te}}")
        continue

    _bodies = []
    for _bi in range(rootComp.bRepBodies.count):
        _b = rootComp.bRepBodies.item(_bi)
        _bb = _b.boundingBox
        try:
            _pp = _b.physicalProperties
            _vol = round(_pp.volume, {VOLUME_PRECISION})
            _area = round(_pp.area, {AREA_PRECISION})
        except:
            _vol = 0.0
            _area = 0.0
        _bodies.append({{
            "name": _b.name,
            "volume": _vol,
            "area": _area,
            "bbox_min": [round(_bb.minPoint.x, {BBOX_PRECISION}), round(_bb.minPoint.y, {BBOX_PRECISION}), round(_bb.minPoint.z, {BBOX_PRECISION})],
            "bbox_max": [round(_bb.maxPoint.x, {BBOX_PRECISION}), round(_bb.maxPoint.y, {BBOX_PRECISION}), round(_bb.maxPoint.z, {BBOX_PRECISION})],
            "faces": _b.faces.count,
            "edges": _b.edges.count,
        }})

    _sketches = []
    for _si in range(rootComp.sketches.count):
        _sk = rootComp.sketches.item(_si)
        _sketches.append({{
            "name": _sk.name,
            "profiles": _sk.profiles.count,
            "curves": _sk.sketchCurves.count,
        }})

    _snapshot = {{
        "step_index": _ti,
        "body_count": len(_bodies),
        "bodies": _bodies,
        "sketch_count": len(_sketches),
        "sketches": _sketches,
        "total_volume": round(sum(_b["volume"] for _b in _bodies), {VOLUME_PRECISION}),
        "total_area": round(sum(_b["area"] for _b in _bodies), {AREA_PRECISION}),
        "total_faces": sum(_b["faces"] for _b in _bodies),
        "total_edges": sum(_b["edges"] for _b in _bodies),
    }}

    _path = os.path.join(_snapshots_dir, f"step_{{_ti}}.json")
    with open(_path, "w") as _f:
        json.dump(_snapshot, _f, ensure_ascii=False, indent=2)

# Restore timeline
try:
    timeline.moveToEnd()
except:
    pass

print(f"Captured {{_total}} snapshots to {{_snapshots_dir}}")
'''


def get_current_state_code() -> str:
    """Return Fusion code that extracts current design state metrics.

    Used for verification — same format as ground truth snapshots.
    """
    return f'''
import json

_bodies = []
for _bi in range(rootComp.bRepBodies.count):
    _b = rootComp.bRepBodies.item(_bi)
    _bb = _b.boundingBox
    try:
        _pp = _b.physicalProperties
        _vol = round(_pp.volume, {VOLUME_PRECISION})
        _area = round(_pp.area, {AREA_PRECISION})
    except:
        _vol = 0.0
        _area = 0.0
    _bodies.append({{
        "name": _b.name,
        "volume": _vol,
        "area": _area,
        "bbox_min": [round(_bb.minPoint.x, {BBOX_PRECISION}), round(_bb.minPoint.y, {BBOX_PRECISION}), round(_bb.minPoint.z, {BBOX_PRECISION})],
        "bbox_max": [round(_bb.maxPoint.x, {BBOX_PRECISION}), round(_bb.maxPoint.y, {BBOX_PRECISION}), round(_bb.maxPoint.z, {BBOX_PRECISION})],
        "faces": _b.faces.count,
        "edges": _b.edges.count,
    }})

_sketches = []
for _si in range(rootComp.sketches.count):
    _sk = rootComp.sketches.item(_si)
    _sketches.append({{
        "name": _sk.name,
        "profiles": _sk.profiles.count,
        "curves": _sk.sketchCurves.count,
    }})

_result = {{
    "body_count": len(_bodies),
    "bodies": _bodies,
    "sketch_count": len(_sketches),
    "sketches": _sketches,
    "total_volume": round(sum(_b["volume"] for _b in _bodies), {VOLUME_PRECISION}),
    "total_area": round(sum(_b["area"] for _b in _bodies), {AREA_PRECISION}),
    "total_faces": sum(_b["faces"] for _b in _bodies),
    "total_edges": sum(_b["edges"] for _b in _bodies),
}}

print("CURRENT_STATE_JSON:" + json.dumps(_result, ensure_ascii=False))
'''


# ---------------------------------------------------------------------------
#  Verification — strict comparison
# ---------------------------------------------------------------------------

def load_ground_truth(step_index: int) -> dict | None:
    """Load cached ground truth for a step from exchange/snapshots/."""
    path = SNAPSHOTS_DIR / f"step_{step_index}.json"
    if not path.exists():
        return None
    with open(path, 'r') as f:
        return json.load(f)


def parse_ground_truth_from_output(output: str) -> dict | None:
    """Extract ground truth JSON from Fusion execution output."""
    for line in output.split('\n'):
        if line.startswith('GROUND_TRUTH_JSON:'):
            return json.loads(line[len('GROUND_TRUTH_JSON:'):])
    return None


def parse_current_state_from_output(output: str) -> dict | None:
    """Extract current state JSON from Fusion execution output."""
    for line in output.split('\n'):
        if line.startswith('CURRENT_STATE_JSON:'):
            return json.loads(line[len('CURRENT_STATE_JSON:'):])
    return None


def save_ground_truth(step_index: int, data: dict):
    """Save ground truth snapshot to exchange/snapshots/."""
    ensure_dirs()
    path = SNAPSHOTS_DIR / f"step_{step_index}.json"
    with open(path, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def verify_step(step_index: int, current_state: dict) -> dict:
    """Compare current state against ground truth for a step.

    Returns:
        {
            "passed": bool,           # True only if ALL metrics match exactly
            "step_index": int,
            "diffs": [                 # list of mismatches (empty if passed)
                {"metric": str, "expected": any, "actual": any},
                ...
            ],
            "summary": str,           # human-readable summary
        }
    """
    ground_truth = load_ground_truth(step_index)
    if ground_truth is None:
        return {
            "passed": False,
            "step_index": step_index,
            "diffs": [{"metric": "ground_truth", "expected": "exists", "actual": "missing"}],
            "summary": f"Step {step_index}: No ground truth found. Run export_step_snapshots first.",
        }

    diffs = []

    # Body count
    if current_state["body_count"] != ground_truth["body_count"]:
        diffs.append({
            "metric": "body_count",
            "expected": ground_truth["body_count"],
            "actual": current_state["body_count"],
        })

    # Total volume
    if current_state["total_volume"] != ground_truth["total_volume"]:
        diffs.append({
            "metric": "total_volume",
            "expected": ground_truth["total_volume"],
            "actual": current_state["total_volume"],
        })

    # Total area
    if current_state["total_area"] != ground_truth["total_area"]:
        diffs.append({
            "metric": "total_area",
            "expected": ground_truth["total_area"],
            "actual": current_state["total_area"],
        })

    # Total faces
    if current_state["total_faces"] != ground_truth["total_faces"]:
        diffs.append({
            "metric": "total_faces",
            "expected": ground_truth["total_faces"],
            "actual": current_state["total_faces"],
        })

    # Total edges
    if current_state["total_edges"] != ground_truth["total_edges"]:
        diffs.append({
            "metric": "total_edges",
            "expected": ground_truth["total_edges"],
            "actual": current_state["total_edges"],
        })

    # Per-body comparison (sorted by volume for stable ordering)
    gt_bodies = sorted(ground_truth["bodies"], key=lambda b: -b["volume"])
    cur_bodies = sorted(current_state["bodies"], key=lambda b: -b["volume"])

    for i, gt_body in enumerate(gt_bodies):
        prefix = f"body[{i}]({gt_body['name']})"
        if i >= len(cur_bodies):
            diffs.append({"metric": f"{prefix}", "expected": "exists", "actual": "missing"})
            continue
        cur_body = cur_bodies[i]

        if cur_body["volume"] != gt_body["volume"]:
            diffs.append({"metric": f"{prefix}.volume", "expected": gt_body["volume"], "actual": cur_body["volume"]})
        if cur_body["area"] != gt_body["area"]:
            diffs.append({"metric": f"{prefix}.area", "expected": gt_body["area"], "actual": cur_body["area"]})
        if cur_body["faces"] != gt_body["faces"]:
            diffs.append({"metric": f"{prefix}.faces", "expected": gt_body["faces"], "actual": cur_body["faces"]})
        if cur_body["edges"] != gt_body["edges"]:
            diffs.append({"metric": f"{prefix}.edges", "expected": gt_body["edges"], "actual": cur_body["edges"]})
        if cur_body["bbox_min"] != gt_body["bbox_min"]:
            diffs.append({"metric": f"{prefix}.bbox_min", "expected": gt_body["bbox_min"], "actual": cur_body["bbox_min"]})
        if cur_body["bbox_max"] != gt_body["bbox_max"]:
            diffs.append({"metric": f"{prefix}.bbox_max", "expected": gt_body["bbox_max"], "actual": cur_body["bbox_max"]})

    # Extra bodies in current state
    for i in range(len(gt_bodies), len(cur_bodies)):
        diffs.append({"metric": f"body[{i}]({cur_bodies[i]['name']})", "expected": "absent", "actual": "exists"})

    # Sketch comparison
    gt_sketches = {s["name"]: s for s in ground_truth.get("sketches", [])}
    cur_sketches = {s["name"]: s for s in current_state.get("sketches", [])}

    for sk_name, gt_sk in gt_sketches.items():
        if sk_name not in cur_sketches:
            diffs.append({"metric": f"sketch({sk_name})", "expected": "exists", "actual": "missing"})
            continue
        cur_sk = cur_sketches[sk_name]
        if cur_sk["profiles"] != gt_sk["profiles"]:
            diffs.append({"metric": f"sketch({sk_name}).profiles", "expected": gt_sk["profiles"], "actual": cur_sk["profiles"]})

    passed = len(diffs) == 0

    if passed:
        summary = f"Step {step_index}: PASS — all metrics match exactly"
    else:
        diff_lines = [f"  {d['metric']}: expected {d['expected']}, got {d['actual']}" for d in diffs[:10]]
        if len(diffs) > 10:
            diff_lines.append(f"  ... and {len(diffs) - 10} more")
        summary = f"Step {step_index}: FAIL — {len(diffs)} mismatches:\n" + "\n".join(diff_lines)

    return {
        "passed": passed,
        "step_index": step_index,
        "diffs": diffs,
        "summary": summary,
    }
