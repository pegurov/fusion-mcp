"""
Pipeline orchestration for step-by-step design reconstruction.

Provides:
- Document management (two-tab workflow: original + reconstruction)
- Per-step reconstruction execution
- Code persistence for regression testing
"""

import json
import os
from pathlib import Path

EXCHANGE_DIR = Path.home() / "fusion-mcp" / "exchange"
DOC_ROLES_FILE = EXCHANGE_DIR / "doc_roles.json"
RECONSTRUCTION_DIR = EXCHANGE_DIR / "reconstruction"
SNAPSHOTS_DIR = EXCHANGE_DIR / "snapshots"
CONTEXT_FILE = RECONSTRUCTION_DIR / "context.json"


def ensure_dirs():
    RECONSTRUCTION_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
#  Document management
# ---------------------------------------------------------------------------

def get_init_reconstruction_code() -> str:
    """Return Fusion code that:
    1. Records current document as 'original'
    2. Creates a new empty document 'Reconstruction'
    3. Records it as 'reconstruction'
    4. Activates the reconstruction document
    """
    doc_roles_path = str(DOC_ROLES_FILE)
    return f'''
import json, os

_roles_path = r"{doc_roles_path}"
os.makedirs(os.path.dirname(_roles_path), exist_ok=True)

# Record original
_orig_doc = app.activeDocument
_orig_name = _orig_doc.name
print(f"Original document: {{_orig_name}}")

# Create new reconstruction document
_new_doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
# Set design type to parametric
_new_design = adsk.fusion.Design.cast(_new_doc.products.itemByProductType('DesignProductType'))
if _new_design:
    _new_design.designType = adsk.fusion.DesignTypes.ParametricDesignType

_recon_name = _new_doc.name
print(f"Reconstruction document: {{_recon_name}}")

# Save roles mapping
_roles = {{
    "original": _orig_name,
    "reconstruction": _recon_name,
}}
with open(_roles_path, "w") as _f:
    json.dump(_roles, _f, indent=2)

print(f"Roles saved to {{_roles_path}}")
print(f"Active document: {{app.activeDocument.name}}")
'''


def get_switch_document_code(role: str) -> str:
    """Return Fusion code that switches to the document with the given role."""
    doc_roles_path = str(DOC_ROLES_FILE)
    return f'''
import json

_roles_path = r"{doc_roles_path}"
with open(_roles_path, "r") as _f:
    _roles = json.load(_f)

_target_name = _roles.get("{role}")
if not _target_name:
    print(f"ERROR: Role '{role}' not found in {{_roles_path}}")
else:
    _found = False
    for _di in range(app.documents.count):
        _doc = app.documents.item(_di)
        if _doc.name == _target_name:
            _doc.activate()
            _found = True
            print(f"Switched to {{_doc.name}} (role: {role})")
            break
    if not _found:
        print(f"ERROR: Document '{{_target_name}}' not found. Available:")
        for _di in range(app.documents.count):
            print(f"  - {{app.documents.item(_di).name}}")
'''


def load_doc_roles() -> dict:
    """Load document roles mapping."""
    if DOC_ROLES_FILE.exists():
        with open(DOC_ROLES_FILE, 'r') as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------------------
#  Per-step reconstruction
# ---------------------------------------------------------------------------

def load_step_snapshot(step_index: int) -> dict | None:
    """Load the construction data snapshot for a step."""
    path = SNAPSHOTS_DIR / f"step_{step_index}.json"
    if not path.exists():
        return None
    with open(path, 'r') as f:
        return json.load(f)


def save_step_code(step_index: int, code: str):
    """Save successfully executed code for a step."""
    ensure_dirs()
    path = RECONSTRUCTION_DIR / f"step_{step_index}_code.py"
    with open(path, 'w') as f:
        f.write(code)


def load_step_code(step_index: int) -> str | None:
    """Load saved code for a step."""
    path = RECONSTRUCTION_DIR / f"step_{step_index}_code.py"
    if not path.exists():
        return None
    with open(path, 'r') as f:
        return f.read()


def save_context(context_dict: dict):
    """Save reconstruction context for resuming."""
    ensure_dirs()
    with open(CONTEXT_FILE, 'w') as f:
        json.dump(context_dict, f, indent=2)


def load_context() -> dict | None:
    """Load saved reconstruction context."""
    if CONTEXT_FILE.exists():
        with open(CONTEXT_FILE, 'r') as f:
            return json.load(f)
    return None


def get_step_count() -> int:
    """Count how many step snapshots exist."""
    if not SNAPSHOTS_DIR.exists():
        return 0
    return len(list(SNAPSHOTS_DIR.glob("step_*.json")))


def get_completed_steps() -> list[int]:
    """Return list of step indices with saved code."""
    if not RECONSTRUCTION_DIR.exists():
        return []
    indices = []
    for f in RECONSTRUCTION_DIR.glob("step_*_code.py"):
        try:
            idx = int(f.stem.split('_')[1])
            indices.append(idx)
        except (ValueError, IndexError):
            pass
    return sorted(indices)
