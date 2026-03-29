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
    1. Identifies the original document (has bodies named 'case'/'cylinder'/'lid')
    2. Closes ALL other documents
    3. Creates a fresh empty reconstruction document
    4. Activates the reconstruction document
    Result: exactly 2 tabs — original (left) + reconstruction (right)
    """
    return '''
import json, os

# Step 1: Find documents WITH and WITHOUT the _recon marker
_original = None
_recon_docs = []
for _di in range(app.documents.count):
    _doc = app.documents.item(_di)
    try:
        _d = _doc.products.itemByProductType('DesignProductType')
        if _d:
            _has_marker = False
            try:
                _has_marker = (_d.userParameters.itemByName("_recon") is not None)
            except:
                pass
            if _has_marker:
                _recon_docs.append(_doc)
            else:
                if _original is None:
                    _original = _doc
    except:
        pass

if not _original:
    _original = app.activeDocument
    print("WARNING: no unmarked document found, using active")

# Step 2: Activate original, close everything else
_original.activate()
_closed = 0
while app.documents.count > 1:
    _found = False
    for _di in range(app.documents.count):
        _doc = app.documents.item(_di)
        if _doc != _original:
            try:
                _doc.close(False)
                _closed += 1
                _found = True
                break
            except:
                pass
    if not _found:
        break
if _closed:
    print(f"Closed {_closed} stale documents")

# Step 3: Create fresh reconstruction document with _recon marker
_new_doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
_new_design = adsk.fusion.Design.cast(_new_doc.products.itemByProductType('DesignProductType'))
if _new_design:
    _new_design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    _new_design.userParameters.add("_recon", adsk.core.ValueInput.createByString("1"), "", "")

_orig_d = adsk.fusion.Design.cast(_original.products.itemByProductType('DesignProductType'))
_orig_tl = _orig_d.timeline.count if _orig_d else 0
print(f"Original: tl={_orig_tl}")
print(f"Reconstruction: fresh (marked)")
print(f"Tabs: {app.documents.count}")
'''


def get_switch_document_code(role: str) -> str:
    """Return Fusion code that switches to the document with the given role.
    Identifies documents by CONTENT, not name:
    - 'original' = document with body named 'case' or timeline >= 40
    - 'reconstruction' = the other document (not original)
    """
    return f'''
# Find documents by _recon marker parameter
_original = None
_reconstruction = None
for _di in range(app.documents.count):
    _doc = app.documents.item(_di)
    try:
        _d = _doc.products.itemByProductType('DesignProductType')
        if _d:
            _has_marker = False
            try:
                _has_marker = (_d.userParameters.itemByName("_recon") is not None)
            except:
                pass
            if _has_marker:
                _reconstruction = _doc
            elif _original is None:
                _original = _doc
    except:
        pass

_role = "{role}"
if _role == "original":
    if _original:
        _original.activate()
        print(f"Switched to {{_original.name}} (role: original)")
    else:
        print("ERROR: Original document not found")
elif _role == "reconstruction":
    if _reconstruction:
        _reconstruction.activate()
        print(f"Switched to {{_reconstruction.name}} (role: reconstruction)")
    else:
        print("ERROR: Reconstruction document not found (no _recon marker). Run init_reconstruction.")

if app.documents.count > 2:
    print(f"WARNING: {{app.documents.count}} tabs open (expected 2). Run init_reconstruction to clean up.")
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
