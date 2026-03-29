"""
Fusion 360 design reconstruction code generator.

Provides:
- generate_single_step(step, context, all_steps) — core: generates code for one step
- _generate_reconstruction_script(data) — wrapper: calls generate_single_step in a loop
- ReconstructionContext — tracks state across per-step generation
- get_helpers_code() — returns the helpers preamble needed by generated code
"""

import math as _math


# ---------------------------------------------------------------------------
#  HELPERS block — shared preamble for all generated scripts
# ---------------------------------------------------------------------------

HELPERS = '''import adsk.core, adsk.fusion, math, traceback

def P(x, y, z=0):
    return adsk.core.Point3D.create(x, y, z)

def VI(v):
    return adsk.core.ValueInput.createByReal(v)

def find_profile(sketch, area, cx, cy, tol_a=0.15, tol_p=0.2, centroid_only=False):
    best, best_s = None, 1e9
    best_pos, best_pos_d = None, 1e9
    for i in range(sketch.profiles.count):
        p = sketch.profiles.item(i)
        try:
            ap = p.areaProperties()
            a = abs(ap.area)
            ad = abs(a - area) / max(area, 1e-6)
            pd = math.sqrt((ap.centroid.x - cx)**2 + (ap.centroid.y - cy)**2)
            if ad < tol_a and pd < tol_p:
                s = ad + pd
                if s < best_s:
                    best, best_s = p, s
            if pd < best_pos_d and a > 1e-6:
                best_pos, best_pos_d = p, pd
        except: pass
    if best:
        return best
    if centroid_only and best_pos and best_pos_d < 1.0:
        return best_pos
    return None

def find_profiles(sketch, targets):
    coll = adsk.core.ObjectCollection.create()
    for a, cx, cy in targets:
        p = find_profile(sketch, a, cx, cy)
        if not p:
            p = find_profile(sketch, a, cx, cy, tol_a=0.3, tol_p=0.5)
        if p: coll.add(p)
    return coll

def find_profile_set(sketch, area, cx, cy, tol_a=0.15):
    """Find one or more profiles whose combined area matches target.
    Handles cases where a single profile in old Fusion is split into
    multiple profiles in newer versions (e.g. arc-line junction splits).
    Only uses strict single-profile match; subset-sum for multi-profile."""
    p = find_profile(sketch, area, cx, cy)
    if p:
        coll = adsk.core.ObjectCollection.create()
        coll.add(p)
        return coll
    cands = []
    for i in range(sketch.profiles.count):
        pi = sketch.profiles.item(i)
        try:
            ap = pi.areaProperties()
            a = abs(ap.area)
            if 1e-6 < a < area * 1.5:
                cands.append((pi, a))
        except: pass
    n = len(cands)
    if n < 2 or n > 12:
        return None
    best, best_score = None, 1e9
    for mask in range(3, 1 << n):
        ta = sum(cands[j][1] for j in range(n) if mask >> j & 1)
        d = abs(ta - area) / max(area, 1e-6)
        if d < tol_a:
            cnt = bin(mask).count('1')
            score = d + cnt * 0.01
            if score < best_score:
                best_score = score
                best = mask
    if best is not None:
        coll = adsk.core.ObjectCollection.create()
        for j in range(n):
            if best >> j & 1:
                coll.add(cands[j][0])
        return coll
    return None

_OP = {0: adsk.fusion.FeatureOperations.JoinFeatureOperation,
       1: adsk.fusion.FeatureOperations.CutFeatureOperation,
       2: adsk.fusion.FeatureOperations.IntersectFeatureOperation,
       3: adsk.fusion.FeatureOperations.NewBodyFeatureOperation}

def do_extrude(comp, profile, distance, operation, symmetric=False):
    ext_input = comp.features.extrudeFeatures.createInput(profile, _OP[operation])
    if symmetric:
        ext_input.setSymmetricExtent(VI(abs(distance)), False)
    else:
        _dir = adsk.fusion.ExtentDirections.PositiveExtentDirection if distance >= 0 else adsk.fusion.ExtentDirections.NegativeExtentDirection
        ext_input.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(VI(abs(distance))), _dir)
    return comp.features.extrudeFeatures.add(ext_input)

def extrude_safe(comp, profile, distance, operation, symmetric=False):
    try:
        return do_extrude(comp, profile, distance, operation, symmetric)
    except Exception as e:
        if 'No target body' in str(e):
            try:
                return do_extrude(comp, profile, -distance, operation, symmetric)
            except Exception as e2:
                if 'No target body' in str(e2) and operation == 1:
                    ext_input = comp.features.extrudeFeatures.createInput(profile, _OP[1])
                    ext_input.setAllExtent(adsk.fusion.ExtentDirections.PositiveExtentDirection)
                    try:
                        return comp.features.extrudeFeatures.add(ext_input)
                    except:
                        ext_input2 = comp.features.extrudeFeatures.createInput(profile, _OP[1])
                        ext_input2.setAllExtent(adsk.fusion.ExtentDirections.NegativeExtentDirection)
                        return comp.features.extrudeFeatures.add(ext_input2)
                raise
        raise

def get_body(comp, name):
    for i in range(comp.bRepBodies.count):
        if comp.bRepBodies.item(i).name == name:
            return comp.bRepBodies.item(i)
    return None

def find_body_for_edges(comp, face_bboxes):
    if not face_bboxes:
        return comp.bRepBodies.item(0) if comp.bRepBodies.count > 0 else None
    tcx = sum((bb[0][0]+bb[1][0])/2 for bb in face_bboxes) / len(face_bboxes)
    tcy = sum((bb[0][1]+bb[1][1])/2 for bb in face_bboxes) / len(face_bboxes)
    tcz = sum((bb[0][2]+bb[1][2])/2 for bb in face_bboxes) / len(face_bboxes)
    best_body, best_score = None, 1e9
    for bi in range(comp.bRepBodies.count):
        body = comp.bRepBodies.item(bi)
        bb = body.boundingBox
        if (bb.minPoint.x - 0.5 <= tcx <= bb.maxPoint.x + 0.5 and
            bb.minPoint.y - 0.5 <= tcy <= bb.maxPoint.y + 0.5 and
            bb.minPoint.z - 0.5 <= tcz <= bb.maxPoint.z + 0.5):
            cx = (bb.minPoint.x + bb.maxPoint.x) / 2
            cy = (bb.minPoint.y + bb.maxPoint.y) / 2
            cz = (bb.minPoint.z + bb.maxPoint.z) / 2
            d = math.sqrt((cx-tcx)**2 + (cy-tcy)**2 + (cz-tcz)**2)
            if d < best_score:
                best_score = d
                best_body = body
    return best_body or (comp.bRepBodies.item(0) if comp.bRepBodies.count > 0 else None)

def find_edges_by_zone(body, face_bboxes, count):
    if not face_bboxes:
        return adsk.core.ObjectCollection.create()
    all_cx, all_cy, all_cz = [], [], []
    zmin_all, zmax_all = 1e9, -1e9
    for bbmin, bbmax in face_bboxes:
        all_cx.append((bbmin[0]+bbmax[0])/2)
        all_cy.append((bbmin[1]+bbmax[1])/2)
        all_cz.append((bbmin[2]+bbmax[2])/2)
        zmin_all = min(zmin_all, bbmin[2])
        zmax_all = max(zmax_all, bbmax[2])
    tz = sum(all_cz)/len(all_cz)
    z_tol = max(0.15, (zmax_all - zmin_all)/2 + 0.1)
    scored = []
    for ei in range(body.edges.count):
        try:
            _ev = body.edges.item(ei).evaluator
            _ok2, _ps, _pe = _ev.getParameterExtents()
            if not _ok2: continue
            ok, pt = _ev.getPointAtParameter((_ps+_pe)/2)
            if ok and abs(pt.z - tz) < z_tol:
                best_d = 1e9
                for cx, cy, cz in zip(all_cx, all_cy, all_cz):
                    d = math.sqrt((pt.x-cx)**2 + (pt.y-cy)**2 + (pt.z-cz)**2)
                    best_d = min(best_d, d)
                scored.append((best_d, ei))
        except: pass
    scored.sort()
    coll = adsk.core.ObjectCollection.create()
    for _, ei in scored[:count]:
        coll.add(body.edges.item(ei))
    return coll

def find_edges_by_bb_fallback(body, bboxes):
    """Match edges to face bboxes by orientation, length, and proximity."""
    edges = adsk.core.ObjectCollection.create()
    used = set()
    for fbb_min, fbb_max in bboxes:
        fc = [(fbb_min[i]+fbb_max[i])/2 for i in range(3)]
        fspan = [fbb_max[i]-fbb_min[i] for i in range(3)]
        dom = fspan.index(max(fspan))
        flen = fspan[dom]
        best_e, best_d = None, 1e9
        for ei in range(body.edges.count):
            if ei in used: continue
            ebb = body.edges.item(ei).boundingBox
            espan = [ebb.maxPoint.x-ebb.minPoint.x, ebb.maxPoint.y-ebb.minPoint.y, ebb.maxPoint.z-ebb.minPoint.z]
            if espan.index(max(espan)) != dom: continue
            if abs(espan[dom] - flen) > 0.3: continue
            mx = (ebb.minPoint.x+ebb.maxPoint.x)/2
            my = (ebb.minPoint.y+ebb.maxPoint.y)/2
            mz = (ebb.minPoint.z+ebb.maxPoint.z)/2
            d = math.sqrt((mx-fc[0])**2+(my-fc[1])**2+(mz-fc[2])**2)
            if d < best_d: best_e, best_d = ei, d
        if best_e is not None:
            edges.add(body.edges.item(best_e))
            used.add(best_e)
    return edges

def auto_combine(comp, target_z_min=None, target_z_max=None):
    if comp.bRepBodies.count <= 1:
        return
    best_bi, best_fc = 0, 0
    for bi in range(comp.bRepBodies.count):
        fc = comp.bRepBodies.item(bi).faces.count
        if fc > best_fc:
            best_fc = fc
            best_bi = bi
    target = comp.bRepBodies.item(best_bi)
    tbb = target.boundingBox
    for bi in range(comp.bRepBodies.count):
        body = comp.bRepBodies.item(bi)
        if body == target:
            continue
        bb = body.boundingBox
        if target_z_min is not None:
            if bb.maxPoint.z < target_z_min - 0.05 or bb.minPoint.z > target_z_max + 0.05:
                continue
        touches = (abs(bb.minPoint.z - tbb.maxPoint.z) < 0.05 or
                   abs(bb.maxPoint.z - tbb.minPoint.z) < 0.05 or
                   (bb.minPoint.z < tbb.maxPoint.z and bb.maxPoint.z > tbb.minPoint.z))
        if not touches:
            continue
        try:
            tools = adsk.core.ObjectCollection.create()
            tools.add(body)
            ci = comp.features.combineFeatures.createInput(target, tools)
            ci.operation = adsk.fusion.FeatureOperations.JoinFeatureOperation
            comp.features.combineFeatures.add(ci)
            return
        except:
            pass

def _face_matches_desc(face, desc, tol=0.15):
    g = face.geometry
    gt = g.objectType.split("::")[-1] if g else ""
    dt = desc.get("type", "")
    if gt != dt:
        # Large-radius Cylinder ≈ Plane: allow match if axis aligns with expected normal
        if dt == "Plane" and gt == "Cylinder" and hasattr(g, 'radius') and g.radius > 2.5:
            if "normal" in desc:
                dn = desc["normal"]
                ax = g.axis
                fwd = (abs(ax.x-dn[0])<tol and abs(ax.y-dn[1])<tol and abs(ax.z-dn[2])<tol)
                rev = (abs(ax.x+dn[0])<tol and abs(ax.y+dn[1])<tol and abs(ax.z+dn[2])<tol)
                return fwd or rev
            return True
        return False
    try:
        if "normal" in desc and hasattr(g, 'normal'):
            dn = desc["normal"]
            nx, ny, nz = g.normal.x, g.normal.y, g.normal.z
            fwd = (abs(nx - dn[0]) < tol and abs(ny - dn[1]) < tol and abs(nz - dn[2]) < tol)
            rev = (abs(nx + dn[0]) < tol and abs(ny + dn[1]) < tol and abs(nz + dn[2]) < tol)
            if not (fwd or rev):
                return False
        elif "radius" in desc and hasattr(g, 'radius'):
            r_tol = max(0.05, desc["radius"] * 0.3)
            if abs(g.radius - desc["radius"]) > r_tol:
                return False
        return True
    except:
        pass
    return gt == dt

def _face_position_score(face, desc):
    if "bb_center" not in desc:
        return 0
    try:
        bb = face.boundingBox
        fc = [(bb.minPoint.x+bb.maxPoint.x)/2, (bb.minPoint.y+bb.maxPoint.y)/2, (bb.minPoint.z+bb.maxPoint.z)/2]
        dc = desc["bb_center"]
        return math.sqrt((fc[0]-dc[0])**2 + (fc[1]-dc[1])**2 + (fc[2]-dc[2])**2)
    except:
        return 0

def _edge_midpoint(edge):
    try:
        _ev = edge.evaluator
        _ok2, _ps, _pe = _ev.getParameterExtents()
        if _ok2:
            _ok, _ept = _ev.getPointAtParameter((_ps+_pe)/2)
            if _ok: return (_ept.x, _ept.y, _ept.z)
    except: pass
    _ebb = edge.boundingBox
    return ((_ebb.minPoint.x+_ebb.maxPoint.x)/2, (_ebb.minPoint.y+_ebb.maxPoint.y)/2, (_ebb.minPoint.z+_ebb.maxPoint.z)/2)

def find_edges_by_descriptors(comp, descriptors, body_name=None):
    coll = adsk.core.ObjectCollection.create()
    if body_name:
        _tb = get_body(comp, body_name)
        _bodies = [_tb] if _tb else [comp.bRepBodies.item(i) for i in range(comp.bRepBodies.count)]
    else:
        _bodies = [comp.bRepBodies.item(i) for i in range(comp.bRepBodies.count)]
    _used = set()
    # Pass 1: face-type match + center proximity
    for desc in descriptors:
        center = desc["center"]
        fa_desc, fb_desc = desc["face_a"], desc.get("face_b")
        best_edge, best_score, best_id = None, 1e9, None
        for body in _bodies:
            for ei in range(body.edges.count):
                edge = body.edges.item(ei)
                _eid = (id(body), ei)
                if _eid in _used: continue
                try:
                    if fb_desc is not None:
                        if edge.faces.count < 2: continue
                        f0, f1 = edge.faces.item(0), edge.faces.item(1)
                        if not ((_face_matches_desc(f0, fa_desc) and _face_matches_desc(f1, fb_desc)) or
                                (_face_matches_desc(f0, fb_desc) and _face_matches_desc(f1, fa_desc))):
                            continue
                    else:
                        _matched_a = False
                        for _fi in range(edge.faces.count):
                            if _face_matches_desc(edge.faces.item(_fi), fa_desc):
                                _matched_a = True
                                break
                        if not _matched_a: continue
                    mx, my, mz = _edge_midpoint(edge)
                    score = math.sqrt((mx-center[0])**2+(my-center[1])**2+(mz-center[2])**2)
                    if score < best_score:
                        best_edge, best_score, best_id = edge, score, _eid
                except: pass
        if best_edge and best_score < 0.5:
            coll.add(best_edge)
            _used.add(best_id)
    # Pass 2: center-only fallback for unmatched descriptors
    if coll.count < len(descriptors):
        _matched = coll.count
        for desc in descriptors:
            center = desc["center"]
            best_edge, best_score, best_id = None, 1e9, None
            for body in _bodies:
                for ei in range(body.edges.count):
                    _eid = (id(body), ei)
                    if _eid in _used: continue
                    edge = body.edges.item(ei)
                    try:
                        mx, my, mz = _edge_midpoint(edge)
                        score = math.sqrt((mx-center[0])**2+(my-center[1])**2+(mz-center[2])**2)
                        if score < best_score:
                            best_edge, best_score, best_id = edge, score, _eid
                    except: pass
            if best_edge and best_score < 0.3 and best_id not in _used:
                coll.add(best_edge)
                _used.add(best_id)
    return coll
'''


def get_helpers_code() -> str:
    """Return the helpers preamble needed by any generated reconstruction code."""
    return HELPERS.strip()


# ---------------------------------------------------------------------------
#  ReconstructionContext — tracks state across per-step generation
# ---------------------------------------------------------------------------

class ReconstructionContext:
    """Mutable state accumulated during step-by-step code generation."""

    def __init__(self):
        self.sketch_vars: dict[str, str] = {}       # sketch_name -> var
        self.sketch_z: dict[str, float] = {}         # sketch_name -> z (cm)
        self.sketch_normal_flipped: dict[str, bool] = {}
        self.sketch_face_flipped: dict[str, bool] = {}   # face axis correction applied
        self.sketch_comp: dict[str, str] = {}        # sketch_name -> comp_var
        self.plane_vars: dict[int, str] = {}         # step_index -> var
        self.plane_comp: dict[int, str] = {}         # step_index -> comp_var
        self.feature_vars: dict[int, str] = {}       # step_index -> var
        self.feature_names: dict[int, str] = {}      # step_index -> fusion feature name
        self.feature_comp: dict[int, str] = {}       # step_index -> comp_var
        self.comp_var: str = "rootComp"
        self.comp_names: dict[str, str] = {}         # component_name -> var
        self.revolve_sketches: set[str] = set()
        self.revolve_profile_centroids: dict[str, list] = {}

    def to_dict(self) -> dict:
        """Serialize for storage/debugging."""
        return {
            "sketch_vars": self.sketch_vars,
            "sketch_z": self.sketch_z,
            "sketch_normal_flipped": self.sketch_normal_flipped,
            "sketch_face_flipped": self.sketch_face_flipped,
            "sketch_comp": self.sketch_comp,
            "plane_vars": {str(k): v for k, v in self.plane_vars.items()},
            "plane_comp": {str(k): v for k, v in self.plane_comp.items()},
            "feature_vars": {str(k): v for k, v in self.feature_vars.items()},
            "feature_names": {str(k): v for k, v in self.feature_names.items()},
            "feature_comp": {str(k): v for k, v in self.feature_comp.items()},
            "comp_var": self.comp_var,
            "comp_names": self.comp_names,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'ReconstructionContext':
        """Deserialize from storage."""
        ctx = cls()
        ctx.sketch_vars = d.get("sketch_vars", {})
        ctx.sketch_z = d.get("sketch_z", {})
        ctx.sketch_normal_flipped = d.get("sketch_normal_flipped", {})
        ctx.sketch_face_flipped = d.get("sketch_face_flipped", {})
        ctx.sketch_comp = d.get("sketch_comp", {})
        ctx.plane_vars = {int(k): v for k, v in d.get("plane_vars", {}).items()}
        ctx.plane_comp = {int(k): v for k, v in d.get("plane_comp", {}).items()}
        ctx.feature_vars = {int(k): v for k, v in d.get("feature_vars", {}).items()}
        ctx.feature_names = {int(k): v for k, v in d.get("feature_names", {}).items()}
        ctx.feature_comp = {int(k): v for k, v in d.get("feature_comp", {}).items()}
        ctx.comp_var = d.get("comp_var", "rootComp")
        ctx.comp_names = d.get("comp_names", {})
        return ctx


def _prescan_revolves(all_steps: list[dict], context: ReconstructionContext):
    """Pre-scan timeline to find sketches used by revolves."""
    for si, step in enumerate(all_steps):
        if step.get('type') == 'RevolveFeature':
            prof = step.get('profile', {})
            for pi in range(si - 1, -1, -1):
                if all_steps[pi].get('type') == 'Sketch':
                    sk_name = all_steps[pi].get('name', '')
                    context.revolve_sketches.add(sk_name)
                    if prof and 'centroid' in prof:
                        if sk_name not in context.revolve_profile_centroids:
                            context.revolve_profile_centroids[sk_name] = []
                        context.revolve_profile_centroids[sk_name].append(prof['centroid'])
                    break


def generate_single_step(step: dict, context: ReconstructionContext, all_steps: list[dict]) -> str:
    """Generate Python code for a single reconstruction step.

    Updates context in-place with new variables.
    Returns a self-contained Python script (with helpers prepended).
    """
    # Pre-scan revolves if not already done
    if not context.revolve_sketches and any(s.get('type') == 'RevolveFeature' for s in all_steps):
        _prescan_revolves(all_steps, context)

    idx = step['index']
    stype = step.get('type', '')
    sname = step.get('name', f'step_{idx}')
    body_name = step.get('body_name', '')

    # Determine active component from export's parent_component field
    parent_comp = step.get('parent_component')
    if parent_comp:
        # Step explicitly belongs to a sub-component
        comp_name_var = context.comp_names.get(parent_comp)
        if comp_name_var:
            context.comp_var = comp_name_var
    elif stype not in ('Occurrence', 'ConstructionPlane') and context.comp_names:
        # No parent_component + sub-components exist = rootComp
        # (ConstructionPlane excluded: API may not report parentComponent reliably)
        if body_name and body_name in context.comp_names:
            context.comp_var = context.comp_names[body_name]
        else:
            context.comp_var = "rootComp"

    comp_var = context.comp_var
    I = ""       # no indent — top level in single step
    I2 = "    "  # single indent

    lines = [get_helpers_code(), "", f"# ── Step {idx}: {sname} ──"]

    # Preamble: look up previously created sketches/planes by name
    # (needed because each exec() has fresh globals)
    preamble_lines = []
    # Look up sub-component variables needed by this step
    _needed_comps = set()
    for sk_name in context.sketch_vars:
        sc = context.sketch_comp.get(sk_name, "rootComp")
        if sc != "rootComp":
            _needed_comps.add(sc)
    for pidx in context.plane_vars:
        pc = context.plane_comp.get(pidx, "rootComp")
        if pc != "rootComp":
            _needed_comps.add(pc)
    if comp_var != "rootComp":
        _needed_comps.add(comp_var)
    for cv in _needed_comps:
        # Find component name from context.comp_names
        cname = next((cn for cn, cv2 in context.comp_names.items() if cv2 == cv), None)
        if cname:
            preamble_lines.append(f'{cv} = None')
            preamble_lines.append(f'for _occ in rootComp.allOccurrences:')
            preamble_lines.append(f'    if _occ.component.name == "{cname}":')
            preamble_lines.append(f'        {cv} = _occ.component; break')
    for sk_name, sk_var in context.sketch_vars.items():
        sc = context.sketch_comp.get(sk_name, "rootComp")
        preamble_lines.append(f'{sk_var} = None')
        preamble_lines.append(f'for _i in range({sc}.sketches.count):')
        preamble_lines.append(f'    if {sc}.sketches.item(_i).name == "{sk_name}":')
        preamble_lines.append(f'        {sk_var} = {sc}.sketches.item(_i); break')
    for pidx, pvar in context.plane_vars.items():
        pc = context.plane_comp.get(pidx, "rootComp")
        plane_name = None
        for s in all_steps:
            if s.get('index') == pidx and s.get('type') == 'ConstructionPlane':
                plane_name = s.get('name')
                break
        if plane_name:
            preamble_lines.append(f'{pvar} = None')
            preamble_lines.append(f'for _i in range({pc}.constructionPlanes.count):')
            preamble_lines.append(f'    if {pc}.constructionPlanes.item(_i).name == "{plane_name}":')
            preamble_lines.append(f'        {pvar} = {pc}.constructionPlanes.item(_i); break')

    # Look up features from timeline (for CircularPattern etc.)
    # Filter by parentComponent to avoid name collisions across components
    for fidx, fvar in context.feature_vars.items():
        fname = context.feature_names.get(fidx)
        if fname:
            fcomp = context.feature_comp.get(fidx, "rootComp")
            preamble_lines.append(f'{fvar} = None')
            preamble_lines.append(f'for _ti in range(design.timeline.count):')
            preamble_lines.append(f'    _ent = design.timeline.item(_ti).entity')
            preamble_lines.append(f'    if hasattr(_ent, "name") and _ent.name == "{fname}":')
            if fcomp != "rootComp":
                preamble_lines.append(f'        if hasattr(_ent, "parentComponent") and _ent.parentComponent == {fcomp}:')
                preamble_lines.append(f'            {fvar} = _ent; break')
            else:
                preamble_lines.append(f'        {fvar} = _ent; break')

    if preamble_lines:
        lines.append("# Look up existing objects from previous steps")
        lines.extend(preamble_lines)
        lines.append("")

    lines.append("try:")

    # Generate step-specific code (same logic as _generate_reconstruction_script)
    _generate_step_body(step, context, all_steps, lines, I2, comp_var)

    lines.append("except Exception as _e:")
    lines.append(f'    print(f"Step {idx}: {sname} ERROR - {{_e}}")')
    lines.append("    import traceback; traceback.print_exc()")

    return '\n'.join(lines)


def _generate_edge_feature_code(step, lines, I2, comp_var, body_name, idx, sname):
    """Generate code for fillet or chamfer — shared edge resolution + feature-specific API."""
    stype = step.get('type', '')
    edges_data = step.get('edge_sets', [])
    edge_descs = step.get('edge_descriptors', [])
    faces = step.get('faces', [])
    if not (edges_data and (edge_descs or faces)):
        lines.append(f'{I2}print("Step {idx}: {sname} SKIPPED - no data")')
        return

    bboxes = [(f['bb_min'], f['bb_max']) for f in faces if 'bb_min' in f]

    # Edge resolution: descriptors (primary) → BB fallback → zone fallback
    if edge_descs:
        n_desc = len(edge_descs)
        lines.append(f"{I2}_edges = find_edges_by_descriptors({comp_var}, {edge_descs}, '{body_name}')")
        if bboxes:
            lines.append(f"{I2}if _edges.count < {n_desc}:")
            lines.append(f"{I2}    _body = get_body({comp_var}, '{body_name}') or find_body_for_edges({comp_var}, {bboxes}) or {comp_var}.bRepBodies.item(0)")
            lines.append(f"{I2}    _edges = find_edges_by_bb_fallback(_body, {bboxes})")
    elif bboxes:
        lines.append(f"{I2}_body = get_body({comp_var}, '{body_name}') or find_body_for_edges({comp_var}, {bboxes}) or {comp_var}.bRepBodies.item(0)")
        lines.append(f"{I2}_edges = find_edges_by_zone(_body, {bboxes}, {len(bboxes)})")
    else:
        lines.append(f"{I2}_edges = adsk.core.ObjectCollection.create()")

    # Feature-specific creation
    if stype == 'FilletFeature':
        r = edges_data[0].get('radius', 0.1)
        lines.append(f"{I2}if _edges.count > 0:")
        lines.append(f"{I2}    _fi={comp_var}.features.filletFeatures.createInput()")
        lines.append(f"{I2}    _fi.addConstantRadiusEdgeSet(_edges, VI({r}), True)")
        lines.append(f"{I2}    try:")
        lines.append(f"{I2}        {comp_var}.features.filletFeatures.add(_fi)")
        lines.append(f'{I2}        print(f"Step {idx}: {sname} r={round(r*10,1)}mm - {{_edges.count}} edges")')
        lines.append(f"{I2}    except Exception as _fe:")
        if edge_descs and bboxes:
            lines.append(f"{I2}        _fb_body = get_body({comp_var}, '{body_name}') or find_body_for_edges({comp_var}, {bboxes}) or {comp_var}.bRepBodies.item(0)")
            lines.append(f"{I2}        _fb_edges = find_edges_by_zone(_fb_body, {bboxes}, {len(bboxes)})")
            lines.append(f"{I2}        if _fb_edges.count > 0:")
            lines.append(f"{I2}            _fi2={comp_var}.features.filletFeatures.createInput()")
            lines.append(f"{I2}            _fi2.addConstantRadiusEdgeSet(_fb_edges, VI({r}), True)")
            lines.append(f"{I2}            {comp_var}.features.filletFeatures.add(_fi2)")
            lines.append(f'{I2}            print(f"Step {idx}: {sname} r={round(r*10,1)}mm - {{_fb_edges.count}} edges (zone fallback)")')
            lines.append(f"{I2}        else:")
            lines.append(f'{I2}            raise _fe')
        else:
            lines.append(f"{I2}        raise")
    else:  # ChamferFeature
        d = edges_data[0].get('distance', 0.1)
        lines.append(f"{I2}if _edges.count > 0:")
        lines.append(f"{I2}    _chi={comp_var}.features.chamferFeatures.createInput2()")
        lines.append(f"{I2}    _chi.chamferEdgeSets.addEqualDistanceChamferEdgeSet(_edges, VI({d}), True)")
        lines.append(f"{I2}    try:")
        lines.append(f"{I2}        {comp_var}.features.chamferFeatures.add(_chi)")
        lines.append(f'{I2}        print(f"Step {idx}: {sname} - {{_edges.count}} edges")')
        lines.append(f"{I2}    except Exception as _ce:")
        if edge_descs and bboxes:
            lines.append(f"{I2}        _fb_body = get_body({comp_var}, '{body_name}') or find_body_for_edges({comp_var}, {bboxes}) or {comp_var}.bRepBodies.item(0)")
            lines.append(f"{I2}        _fb_edges = find_edges_by_zone(_fb_body, {bboxes}, {len(bboxes)})")
            lines.append(f"{I2}        if _fb_edges.count > 0:")
            lines.append(f"{I2}            _chi2={comp_var}.features.chamferFeatures.createInput2()")
            lines.append(f"{I2}            _chi2.chamferEdgeSets.addEqualDistanceChamferEdgeSet(_fb_edges, VI({d}), True)")
            lines.append(f"{I2}            {comp_var}.features.chamferFeatures.add(_chi2)")
            lines.append(f'{I2}            print(f"Step {idx}: {sname} - {{_fb_edges.count}} edges (zone fallback)")')
            lines.append(f"{I2}        else:")
            lines.append(f'{I2}            raise _ce')
        else:
            lines.append(f"{I2}        raise")

    lines.append(f"{I2}else:")
    lines.append(f'{I2}    print("Step {idx}: {sname} SKIPPED - no edges found")')


def _generate_step_body(step, context, all_steps, lines, I2, comp_var):
    """Generate the body of a single step (shared between single-step and full-script modes)."""
    idx = step['index']
    stype = step.get('type', '')
    sname = step.get('name', f'step_{idx}')
    body_name = step.get('body_name', '')

    if stype == 'Sketch':
        var = f"sk_{idx}"
        context.sketch_vars[sname] = var
        context.sketch_comp[sname] = comp_var
        plane_ref = step.get('plane', 'XY')
        plane_origin = step.get('plane_origin')
        context.sketch_z[sname] = plane_origin[2] if plane_origin else 0
        pn = step.get('plane_normal')
        is_construction_plane = plane_ref not in ('XY', 'XZ', 'YZ', 'face')
        context.sketch_normal_flipped[sname] = (is_construction_plane and pn is not None and pn[2] < -0.5)

        if plane_ref == 'XY':
            lines.append(f"{I2}{var} = {comp_var}.sketches.add({comp_var}.xYConstructionPlane)")
        elif plane_ref == 'XZ':
            lines.append(f"{I2}{var} = {comp_var}.sketches.add({comp_var}.xZConstructionPlane)")
        elif plane_ref == 'YZ':
            lines.append(f"{I2}{var} = {comp_var}.sketches.add({comp_var}.yZConstructionPlane)")
        elif plane_ref == 'face':
            fz = step.get('face_center_z', 0)
            fnz = step.get('face_normal', [0,0,1])[2]
            fa = step.get('face_area', 0)
            lines.append(f"{I2}_face = None")
            lines.append(f"{I2}_face_score = 1e9")
            lines.append(f"{I2}for _bi in range({comp_var}.bRepBodies.count):")
            lines.append(f"{I2}    _b = {comp_var}.bRepBodies.item(_bi)")
            lines.append(f"{I2}    for _fi in range(_b.faces.count):")
            lines.append(f"{I2}        try:")
            lines.append(f"{I2}            _g = _b.faces.item(_fi).geometry")
            lines.append(f"{I2}            if hasattr(_g,'normal') and abs(_g.normal.z-({fnz}))<0.15:")
            lines.append(f"{I2}                _zc=(_b.faces.item(_fi).boundingBox.minPoint.z+_b.faces.item(_fi).boundingBox.maxPoint.z)/2")
            lines.append(f"{I2}                if abs(_zc-{fz})<0.15:")
            if fa > 0:
                lines.append(f"{I2}                    _ad = abs(_b.faces.item(_fi).area - {fa})")
                lines.append(f"{I2}                    if _ad < _face_score: _face=_b.faces.item(_fi); _face_score=_ad")
            else:
                lines.append(f"{I2}                    _face=_b.faces.item(_fi)")
            lines.append(f"{I2}        except: pass")
            lines.append(f"{I2}if _face:")
            lines.append(f"{I2}    {var} = {comp_var}.sketches.addWithoutEdges(_face)")
            lines.append(f"{I2}else:")
            lines.append(f"{I2}    _pi={comp_var}.constructionPlanes.createInput()")
            lines.append(f"{I2}    _pi.setByOffset({comp_var}.xYConstructionPlane, VI({fz}))")
            lines.append(f"{I2}    {var}={comp_var}.sketches.add({comp_var}.constructionPlanes.add(_pi))")
        else:
            found = None
            if plane_origin and context.plane_vars:
                best_dist = 1e9
                for pidx, pvar in context.plane_vars.items():
                    pstep = all_steps[pidx] if pidx < len(all_steps) else {}
                    geo = pstep.get('geometry_origin')
                    p_z = geo[2] if geo else pstep.get('offset', 0)
                    d = abs(p_z - plane_origin[2])
                    if d < best_dist:
                        best_dist = d
                        found = f"plane_{pidx}"
            if not found:
                for pidx, pvar in context.plane_vars.items():
                    pstep = all_steps[pidx] if pidx < len(all_steps) else {}
                    if pstep.get('name', '') == plane_ref:
                        found = f"plane_{pidx}"
                        break
            if found:
                lines.append(f"{I2}{var} = {comp_var}.sketches.add({found})")
            else:
                lines.append(f"{I2}# WARNING: plane '{plane_ref}' not resolved")
                lines.append(f"{I2}{var} = {comp_var}.sketches.add({comp_var}.xYConstructionPlane)")

        lines.append(f'{I2}{var}.name = "{sname}"')

        # Face sketch axis correction: detect if sketch axes are flipped vs export
        exp_xa = step.get('sketch_x_axis')
        face_axis_correction = (plane_ref == 'face' and exp_xa is not None)
        # Track if face axis will flip (for centroid correction in extrudes)
        if face_axis_correction and exp_xa[0] < -0.5:
            context.sketch_face_flipped[sname] = True
        if face_axis_correction:
            lines.append(f"{I2}# Detect face sketch axis flip")
            lines.append(f"{I2}_orig_P = P")
            lines.append(f"{I2}_act_t = {var}.transform")
            lines.append(f"{I2}_dot_x = {exp_xa[0]}*_act_t.getCell(0,0) + {exp_xa[1]}*_act_t.getCell(1,0)")
            lines.append(f"{I2}if _dot_x < -0.5:")
            lines.append(f"{I2}    def P(x, y, z=0): return _orig_P(-x, -y, z)")
            lines.append(f'{I2}    print("  Face axis correction: flipping XY")')

        curves = step.get('curves', [])
        mirror_x = context.sketch_normal_flipped.get(sname, False)
        if curves:
            lines.append(f"{I2}{var}.isComputeDeferred = True")
            for c in curves:
                ct = c.get('type', '')
                ic = c.get('construction', False)
                if ct == 'SketchCircle':
                    cx, cy = c['center']
                    if mirror_x: cx = -cx
                    lines.append(f"{I2}{var}.sketchCurves.sketchCircles.addByCenterRadius(P({cx},{cy}),{c['radius']})")
                elif ct == 'SketchLine':
                    sx, sy = c['start']; ex, ey = c['end']
                    if mirror_x: sx, ex = -sx, -ex
                    if ic:
                        lines.append(f"{I2}_cl={var}.sketchCurves.sketchLines.addByTwoPoints(P({sx},{sy}),P({ex},{ey})); _cl.isConstruction=True")
                    else:
                        lines.append(f"{I2}{var}.sketchCurves.sketchLines.addByTwoPoints(P({sx},{sy}),P({ex},{ey}))")
                elif ct == 'SketchArc':
                    cx, cy = c['center']; r = c['radius']
                    sx, sy = c['start']; ex, ey = c['end']
                    if mirror_x: cx, sx, ex = -cx, -sx, -ex
                    sweep = _math.atan2(ey-cy, ex-cx) - _math.atan2(sy-cy, sx-cx)
                    if sweep > _math.pi: sweep -= 2*_math.pi
                    elif sweep < -_math.pi: sweep += 2*_math.pi
                    # Disambiguate sweep direction using exported midpoint
                    # (handles ALL cases: 90° vs 270°, not just ±180°)
                    mid = c.get('mid')
                    if mid:
                        mx_exp, my_exp = mid[0], mid[1]
                        if mirror_x: mx_exp = -mx_exp
                        start_a = _math.atan2(sy-cy, sx-cx)
                        mid_angle = start_a + sweep/2
                        mx_calc = cx + r * _math.cos(mid_angle)
                        my_calc = cy + r * _math.sin(mid_angle)
                        d_cur = (mx_calc - mx_exp)**2 + (my_calc - my_exp)**2
                        alt_sweep = (sweep - 2*_math.pi) if sweep > 0 else (sweep + 2*_math.pi)
                        alt_mid_angle = start_a + alt_sweep/2
                        mx_alt = cx + r * _math.cos(alt_mid_angle)
                        my_alt = cy + r * _math.sin(alt_mid_angle)
                        d_alt = (mx_alt - mx_exp)**2 + (my_alt - my_exp)**2
                        if d_alt < d_cur:
                            sweep = alt_sweep
                    if ic:
                        lines.append(f"{I2}_cl={var}.sketchCurves.sketchArcs.addByCenterStartSweep(P({cx},{cy}),P({sx},{sy}),{round(sweep,6)}); _cl.isConstruction=True")
                    else:
                        lines.append(f"{I2}{var}.sketchCurves.sketchArcs.addByCenterStartSweep(P({cx},{cy}),P({sx},{sy}),{round(sweep,6)})")
            lines.append(f"{I2}{var}.isComputeDeferred = False")
        if face_axis_correction:
            lines.append(f"{I2}P = _orig_P  # restore P after face axis correction")
        lines.append(f'{I2}print(f"Step {idx}: {sname} - {{{var}.profiles.count}} profiles")')

    elif stype == 'ExtrudeFeature':
        var = f"ext_{idx}"
        context.feature_vars[idx] = var
        context.feature_names[idx] = sname
        context.feature_comp[idx] = comp_var
        lines.append(f"{I2}{var} = None")
        sk_name = step.get('sketch_name', '')
        sk_var = context.sketch_vars.get(sk_name, 'sk_0')
        profiles = step.get('profiles', [])
        op = step.get('operation', 0)
        dist = step.get('distance', 0)
        is_sym = 'Symmetric' in step.get('extent_type', '')

        # Preserve original distance — no clamping

        body_z_min = step.get('body_z_min')
        body_z_max = step.get('body_z_max')
        sk_z = context.sketch_z.get(sk_name, 0)
        flipped = context.sketch_normal_flipped.get(sk_name, False)
        # Detect real normal flip from step data (covers both construction planes and faces)
        spn = step.get('sketch_plane_normal')
        normal_flipped = (spn is not None and spn[2] < -0.5) or flipped
        # For direction: construction planes from XY offset always have normal [0,0,1]
        # in reconstruction, so normal_flipped should NOT affect direction sign.
        # Only face sketches have reconstruction normal matching original.
        recon_normal_flipped = normal_flipped and not flipped
        orig_dist = step.get('distance', 0)
        if not is_sym and spn is not None and abs(spn[2]) > 0.5:
            # Primary method: derive direction from original distance sign + normal
            goes_up = (orig_dist < 0) == (spn[2] < 0)
            if goes_up:
                dist = abs(dist) if not recon_normal_flipped else -abs(dist)
            else:
                dist = -abs(dist) if not recon_normal_flipped else abs(dist)
        elif (flipped or normal_flipped) and op in (0, 1):
            goes_up = step.get('extrude_goes_up')
            if goes_up is not None:
                if (goes_up and dist < 0) or (not goes_up and dist > 0):
                    dist = -dist

        fn = "do_extrude" if op == 3 else "extrude_safe"
        sym = ", symmetric=True" if is_sym else ""
        cx_sign = -1 if context.sketch_normal_flipped.get(sk_name, False) else 1
        # Face axis correction flips BOTH x and y
        face_flipped = context.sketch_face_flipped.get(sk_name, False)
        if face_flipped:
            cx_sign = -1
        cy_sign = -1 if face_flipped else 1

        if len(profiles) == 1:
            p = profiles[0]
            _cx = round(p['centroid'][0] * cx_sign, 4)
            _cy = round(p['centroid'][1] * cy_sign, 4)
            lines.append(f"{I2}_p = find_profile({sk_var}, {p['area']}, {_cx}, {_cy})")
            lines.append(f"{I2}if _p:")
            lines.append(f'{I2}    {var} = {fn}({comp_var}, _p, {dist}, {op}{sym})')
            lines.append(f'{I2}    {var}.name = "{sname}"')
            lines.append(f'{I2}    print("Step {idx}: {sname}")')
            lines.append(f"{I2}else:")
            lines.append(f"{I2}    _pset = find_profile_set({sk_var}, {p['area']}, {_cx}, {_cy})")
            lines.append(f"{I2}    if _pset:")
            lines.append(f'{I2}        {var} = {fn}({comp_var}, _pset, {dist}, {op}{sym})')
            lines.append(f'{I2}        {var}.name = "{sname}"')
            lines.append(f'{I2}        print(f"Step {idx}: {sname} ({{_pset.count}} profiles aggregated)")')
            lines.append(f"{I2}    else:")
            lines.append(f"{I2}        _p = find_profile({sk_var}, {p['area']}, {_cx}, {_cy}, tol_a=0.3, tol_p=0.5)")
            lines.append(f"{I2}        if not _p: _p = find_profile({sk_var}, {p['area']}, {_cx}, {_cy}, centroid_only=True)")
            lines.append(f"{I2}        if _p:")
            lines.append(f'{I2}            {var} = {fn}({comp_var}, _p, {dist}, {op}{sym})')
            lines.append(f'{I2}            {var}.name = "{sname}"')
            lines.append(f'{I2}            print("Step {idx}: {sname} (relaxed)")')
            lines.append(f"{I2}        else:")
            lines.append(f'{I2}            print("Step {idx}: {sname} FAILED")')
        elif len(profiles) > 1:
            targets = [(p['area'], round(p['centroid'][0] * cx_sign, 4), round(p['centroid'][1] * cy_sign, 4)) for p in profiles]
            lines.append(f"{I2}_profs = find_profiles({sk_var}, {targets})")
            lines.append(f"{I2}if _profs.count > 0:")
            lines.append(f'{I2}    {var} = {fn}({comp_var}, _profs, {dist}, {op}{sym})')
            lines.append(f'{I2}    {var}.name = "{sname}"')
            lines.append(f'{I2}    print(f"Step {idx}: {sname} - {{_profs.count}}/{len(profiles)}")')
            lines.append(f"{I2}else:")
            lines.append(f'{I2}    print("Step {idx}: {sname} FAILED")')
        else:
            lines.append(f'{I2}print("Step {idx}: {sname} SKIPPED - no profiles")')

    elif stype == 'ConstructionPlane':
        var = f"plane_{idx}"
        context.plane_vars[idx] = var
        context.plane_comp[idx] = comp_var
        geo_origin = step.get('geometry_origin')
        if geo_origin:
            abs_z = geo_origin[2]
        else:
            offset = step.get('offset', 0)
            parent_z = step.get('parent_z', 0)
            parent = step.get('parent', '')
            abs_z = (parent_z + offset) if parent == 'face' else offset
        lines.append(f"{I2}_pi = {comp_var}.constructionPlanes.createInput()")
        lines.append(f"{I2}_pi.setByOffset({comp_var}.xYConstructionPlane, VI({round(abs_z, 6)}))")
        lines.append(f"{I2}{var} = {comp_var}.constructionPlanes.add(_pi)")
        lines.append(f'{I2}{var}.name = "{sname}"')
        lines.append(f'{I2}print("Step {idx}: {sname} z={round(abs_z*10,1)}mm")')

    elif stype == 'Occurrence':
        cname = step.get('component_name', 'component')
        cvar = f"comp_{cname.replace('-','_').replace(' ','_')}"
        context.comp_names[cname] = cvar
        context.comp_var = cvar
        lines.append(f"{I2}_occ = rootComp.occurrences.addNewComponent(adsk.core.Matrix3D.create())")
        lines.append(f"{I2}{cvar} = _occ.component")
        lines.append(f'{I2}{cvar}.name = "{cname}"')
        lines.append(f'{I2}print("Step {idx}: {cname} (sub-component)")')

    elif stype in ('FilletFeature', 'ChamferFeature'):
        _generate_edge_feature_code(step, lines, I2, comp_var, body_name, idx, sname)

    elif stype == 'RevolveFeature':
        var = f"rev_{idx}"
        context.feature_vars[idx] = var
        context.feature_names[idx] = sname
        context.feature_comp[idx] = comp_var
        op = step.get('operation', 1)
        angle = step.get('angle', 360.0)
        prof = step.get('profile', {})
        axis_dir = step.get('axis_direction')
        op_names = {0:'JoinFeatureOperation',1:'CutFeatureOperation',3:'NewBodyFeatureOperation'}
        op_name = op_names.get(op, 'CutFeatureOperation')
        prev_sk = None
        for pi in range(idx-1, -1, -1):
            ps = all_steps[pi]
            if ps.get('type') == 'Sketch':
                prev_sk = context.sketch_vars.get(ps.get('name'))
                break
        angle_rad = round(_math.radians(angle), 6)
        lines.append(f"{I2}{var} = None")

        if axis_dir:
            if abs(axis_dir[2]) > 0.9:
                axis_expr = f"{comp_var}.zConstructionAxis"
            elif abs(axis_dir[1]) > 0.9:
                axis_expr = f"{comp_var}.yConstructionAxis"
            else:
                axis_expr = f"{comp_var}.xConstructionAxis"
        else:
            axis_expr = None

        if prev_sk:
            multi_profiles = step.get('profiles', [])
            if multi_profiles and len(multi_profiles) > 1:
                # Multi-profile revolve: find each profile and add to ObjectCollection
                targets = [(p['area'], p['centroid'][0], p['centroid'][1]) for p in multi_profiles]
                lines.append(f"{I2}_p = find_profiles({prev_sk}, {targets})")
            elif prof:
                a = prof.get('area', 0)
                cx, cy = prof.get('centroid', [0,0])
                lines.append(f"{I2}_p = find_profile({prev_sk}, {a}, {cx}, {cy}, tol_a=0.3, tol_p=0.3)")
            else:
                lines.append(f"{I2}_p = {prev_sk}.profiles.item(0)")

            if axis_expr:
                lines.append(f"{I2}_ri = {comp_var}.features.revolveFeatures.createInput(_p, {axis_expr}, adsk.fusion.FeatureOperations.{op_name})")
                lines.append(f"{I2}_ri.setAngleExtent(False, VI({angle_rad}))")
                lines.append(f"{I2}{var} = {comp_var}.features.revolveFeatures.add(_ri)")
                lines.append(f'{I2}{var}.name = "{sname}"')
                lines.append(f'{I2}print("Step {idx}: {sname}")')
            else:
                lines.append(f"{I2}for _ax in [{comp_var}.zConstructionAxis, {comp_var}.yConstructionAxis, {comp_var}.xConstructionAxis]:")
                lines.append(f"{I2}    try:")
                lines.append(f"{I2}        _ri = {comp_var}.features.revolveFeatures.createInput(_p, _ax, adsk.fusion.FeatureOperations.{op_name})")
                lines.append(f"{I2}        _ri.setAngleExtent(False, VI({angle_rad}))")
                lines.append(f"{I2}        {var} = {comp_var}.features.revolveFeatures.add(_ri)")
                lines.append(f'{I2}        {var}.name = "{sname}"')
                lines.append(f'{I2}        print("Step {idx}: {sname}"); break')
                lines.append(f"{I2}    except: pass")
                lines.append(f"{I2}else:")
                lines.append(f'{I2}    print("Step {idx}: {sname} FAILED - all axes")')
        else:
            lines.append(f'{I2}print("Step {idx}: {sname} SKIPPED - no sketch")')

    elif stype == 'CircularPatternFeature':
        qty = step.get('quantity', 2)
        axis_dir = step.get('axis_direction')
        ta = round(_math.radians(step.get('total_angle', 360)), 6)

        # Collect features to pattern
        input_indices = step.get('input_timeline_indices', [])
        if input_indices:
            prev_feats = [context.feature_vars[fi] for fi in input_indices if fi in context.feature_vars]
        else:
            # Fallback: use recent features from context
            all_feats = [(fi, context.feature_vars[fi]) for fi in sorted(context.feature_vars) if fi < idx and fi > idx - 5]
            rev_feats = [fv for fi, fv in all_feats if fv.startswith('rev_')]
            if rev_feats:
                prev_feats = rev_feats
            else:
                # Use only the immediately previous feature
                prev_one = [(fi, fv) for fi, fv in all_feats if fi == idx - 1]
                prev_feats = [fv for _, fv in prev_one] if prev_one else [fv for _, fv in all_feats[-1:]]

        if prev_feats:
            lines.append(f"{I2}_pc = adsk.core.ObjectCollection.create()")
            for fv in prev_feats:
                lines.append(f"{I2}if {fv}: _pc.add({fv})")
            lines.append(f"{I2}if _pc.count > 0:")

            if axis_dir:
                if abs(axis_dir[2]) > 0.9:
                    primary_axis = f"{comp_var}.zConstructionAxis"
                elif abs(axis_dir[1]) > 0.9:
                    primary_axis = f"{comp_var}.yConstructionAxis"
                else:
                    primary_axis = f"{comp_var}.xConstructionAxis"
                lines.append(f"{I2}    _pi = {comp_var}.features.circularPatternFeatures.createInput(_pc, {primary_axis})")
                lines.append(f"{I2}    _pi.quantity = VI({qty})")
                lines.append(f"{I2}    _pi.totalAngle = VI({ta})")
                lines.append(f"{I2}    {comp_var}.features.circularPatternFeatures.add(_pi)")
                lines.append(f'{I2}    print("Step {idx}: {sname} x{qty}")')
            else:
                lines.append(f"{I2}    for _ax in [{comp_var}.zConstructionAxis, {comp_var}.yConstructionAxis, {comp_var}.xConstructionAxis]:")
                lines.append(f"{I2}        try:")
                lines.append(f"{I2}            _pi = {comp_var}.features.circularPatternFeatures.createInput(_pc, _ax)")
                lines.append(f"{I2}            _pi.quantity = VI({qty})")
                lines.append(f"{I2}            _pi.totalAngle = VI({ta})")
                lines.append(f"{I2}            {comp_var}.features.circularPatternFeatures.add(_pi)")
                lines.append(f'{I2}            print("Step {idx}: {sname} x{qty}"); break')
                lines.append(f"{I2}        except: pass")
                lines.append(f"{I2}    else:")
                lines.append(f'{I2}        print("Step {idx}: {sname} FAILED - all axes")')
            lines.append(f"{I2}else:")
            lines.append(f'{I2}    print("Step {idx}: {sname} SKIPPED - no features")')
        else:
            lines.append(f'{I2}print("Step {idx}: {sname} SKIPPED - no features to pattern")')

    elif stype == 'ShellFeature':
        thickness = step.get('inside_thickness', 0.2)
        removed = step.get('removed_faces', [])
        if removed:
            # Find the face to remove by geometry descriptor
            rd = removed[0]  # Usually one face removed
            lines.append(f"{I2}_shell_face = None")
            lines.append(f"{I2}_shell_score = 1e9")
            lines.append(f"{I2}for _bi in range({comp_var}.bRepBodies.count):")
            lines.append(f"{I2}    _b = {comp_var}.bRepBodies.item(_bi)")
            lines.append(f"{I2}    for _fi in range(_b.faces.count):")
            lines.append(f"{I2}        _f = _b.faces.item(_fi)")
            lines.append(f"{I2}        try:")
            lines.append(f"{I2}            _g = _f.geometry")
            if 'normal' in rd:
                n = rd['normal']
                lines.append(f"{I2}            if hasattr(_g, 'normal') and abs(_g.normal.x-({n[0]}))<0.15 and abs(_g.normal.y-({n[1]}))<0.15 and abs(_g.normal.z-({n[2]}))<0.15:")
            else:
                lines.append(f"{I2}            if True:")
            lines.append(f"{I2}                _ad = abs(_f.area - {rd['area']})")
            lines.append(f"{I2}                if _ad < _shell_score:")
            lines.append(f"{I2}                    _shell_face = _f")
            lines.append(f"{I2}                    _shell_score = _ad")
            lines.append(f"{I2}        except: pass")
            lines.append(f"{I2}if _shell_face:")
            lines.append(f"{I2}    _sc = adsk.core.ObjectCollection.create()")
            lines.append(f"{I2}    _sc.add(_shell_face)")
            lines.append(f"{I2}    _si = {comp_var}.features.shellFeatures.createInput(_sc, False)")
            lines.append(f"{I2}    _si.insideThickness = VI({thickness})")
            lines.append(f"{I2}    {comp_var}.features.shellFeatures.add(_si)")
            lines.append(f'{I2}    print("Step {idx}: {sname} t={round(thickness*10,1)}mm")')
            lines.append(f"{I2}else:")
            lines.append(f'{I2}    print("Step {idx}: {sname} FAILED - face not found")')
        else:
            lines.append(f'{I2}print("Step {idx}: {sname} SKIPPED - no face data")')

    elif stype == 'CombineFeature':
        op = step.get('operation', 0)
        target = step.get('target_body', '')
        tools = step.get('tool_bodies', [])
        keep = step.get('is_keep_tools', False)
        if target and tools:
            lines.append(f"{I2}_target = get_body({comp_var}, '{target}')")
            lines.append(f"{I2}_tools = adsk.core.ObjectCollection.create()")
            for tb in tools:
                lines.append(f"{I2}_tb = get_body({comp_var}, '{tb}')")
                lines.append(f"{I2}if _tb: _tools.add(_tb)")
            lines.append(f"{I2}if _target and _tools.count > 0:")
            lines.append(f"{I2}    _ci = {comp_var}.features.combineFeatures.createInput(_target, _tools)")
            lines.append(f"{I2}    _ci.operation = _OP[{op}]")
            lines.append(f"{I2}    _ci.isKeepToolBodies = {keep}")
            lines.append(f"{I2}    {comp_var}.features.combineFeatures.add(_ci)")
            lines.append(f'{I2}    print("Step {idx}: {sname}")')
            lines.append(f"{I2}else:")
            lines.append(f'{I2}    print("Step {idx}: {sname} FAILED - bodies not found")')
        else:
            lines.append(f'{I2}print("Step {idx}: {sname} SKIPPED - no data")')

    elif stype == 'MirrorFeature':
        op = step.get('operation', 0)
        plane_type = step.get('mirror_plane_type', '')
        plane_name = step.get('mirror_plane_name', '')
        plane_normal = step.get('mirror_plane_normal')
        input_indices = step.get('input_timeline_indices', [])
        # Determine mirror plane
        if plane_name in ('XY', 'XZ', 'YZ') or plane_type == 'ConstructionPlane':
            if 'XY' in plane_name or 'XY' in str(step):
                plane_expr = f"{comp_var}.xYConstructionPlane"
            elif 'XZ' in plane_name or (plane_normal and abs(plane_normal[1]) > 0.9):
                plane_expr = f"{comp_var}.xZConstructionPlane"
            elif 'YZ' in plane_name or (plane_normal and abs(plane_normal[0]) > 0.9):
                plane_expr = f"{comp_var}.yZConstructionPlane"
            else:
                # Custom construction plane — look up by name
                plane_expr = None
                for pidx, pvar in context.plane_vars.items():
                    pstep = all_steps[pidx] if pidx < len(all_steps) else {}
                    if pstep.get('name', '') == plane_name:
                        plane_expr = f"plane_{pidx}"
                        break
                if not plane_expr:
                    plane_expr = f"{comp_var}.xYConstructionPlane"
        elif plane_normal:
            if abs(plane_normal[0]) > 0.9:
                plane_expr = f"{comp_var}.yZConstructionPlane"
            elif abs(plane_normal[1]) > 0.9:
                plane_expr = f"{comp_var}.xZConstructionPlane"
            else:
                plane_expr = f"{comp_var}.xYConstructionPlane"
        else:
            plane_expr = f"{comp_var}.xYConstructionPlane"

        # Collect input features
        prev_feats = [context.feature_vars[fi] for fi in input_indices if fi in context.feature_vars]
        if not prev_feats:
            # Fallback: use most recent feature
            all_fi = sorted(fi for fi in context.feature_vars if fi < idx)
            if all_fi:
                prev_feats = [context.feature_vars[all_fi[-1]]]

        if prev_feats:
            lines.append(f"{I2}_mc = adsk.core.ObjectCollection.create()")
            for fv in prev_feats:
                lines.append(f"{I2}if {fv}: _mc.add({fv})")
            lines.append(f"{I2}if _mc.count > 0:")
            lines.append(f"{I2}    _mi = {comp_var}.features.mirrorFeatures.createInput(_mc, {plane_expr})")
            op_names = {0:'JoinFeatureOperation', 1:'CutFeatureOperation', 3:'NewBodyFeatureOperation'}
            op_name = op_names.get(op, 'JoinFeatureOperation')
            lines.append(f"{I2}    _mi.operation = adsk.fusion.FeatureOperations.{op_name}")
            lines.append(f"{I2}    {comp_var}.features.mirrorFeatures.add(_mi)")
            lines.append(f'{I2}    print("Step {idx}: {sname}")')
            lines.append(f"{I2}else:")
            lines.append(f'{I2}    print("Step {idx}: {sname} SKIPPED - no features")')
        else:
            lines.append(f'{I2}print("Step {idx}: {sname} SKIPPED - no input features")')

    elif stype == 'OffsetFacesFeature':
        dist = step.get('offset_distance', 0)
        faces_data = step.get('faces', [])
        if faces_data and dist != 0:
            lines.append(f"{I2}_of_faces = adsk.core.ObjectCollection.create()")
            for fd in faces_data:
                bc = fd.get('bb_center', [0,0,0])
                n = fd.get('normal')
                lines.append(f"{I2}for _bi in range({comp_var}.bRepBodies.count):")
                lines.append(f"{I2}    for _fi in range({comp_var}.bRepBodies.item(_bi).faces.count):")
                lines.append(f"{I2}        _f = {comp_var}.bRepBodies.item(_bi).faces.item(_fi)")
                lines.append(f"{I2}        _bb = _f.boundingBox")
                lines.append(f"{I2}        _cx = (_bb.minPoint.x+_bb.maxPoint.x)/2")
                lines.append(f"{I2}        _cy = (_bb.minPoint.y+_bb.maxPoint.y)/2")
                lines.append(f"{I2}        _cz = (_bb.minPoint.z+_bb.maxPoint.z)/2")
                if n:
                    lines.append(f"{I2}        _g = _f.geometry")
                    lines.append(f"{I2}        if hasattr(_g,'normal') and abs(_g.normal.x-({n[0]}))<0.15 and abs(_g.normal.y-({n[1]}))<0.15 and abs(_g.normal.z-({n[2]}))<0.15:")
                    lines.append(f"{I2}            if abs(_cx-({bc[0]}))<0.1 and abs(_cy-({bc[1]}))<0.1 and abs(_cz-({bc[2]}))<0.1:")
                    lines.append(f"{I2}                _of_faces.add(_f)")
                else:
                    lines.append(f"{I2}        if abs(_cx-({bc[0]}))<0.1 and abs(_cy-({bc[1]}))<0.1 and abs(_cz-({bc[2]}))<0.1:")
                    lines.append(f"{I2}            if abs(_f.area - {fd.get('area',0)}) < 0.1:")
                    lines.append(f"{I2}                _of_faces.add(_f)")
            lines.append(f"{I2}if _of_faces.count > 0:")
            lines.append(f"{I2}    _oi = {comp_var}.features.offsetFacesFeatures.createInput(_of_faces, VI({dist}))")
            lines.append(f"{I2}    {comp_var}.features.offsetFacesFeatures.add(_oi)")
            lines.append(f'{I2}    print(f"Step {idx}: {sname} {{_of_faces.count}} faces")')
            lines.append(f"{I2}else:")
            lines.append(f'{I2}    print("Step {idx}: {sname} FAILED - faces not found")')
        else:
            lines.append(f'{I2}print("Step {idx}: {sname} SKIPPED - no data")')

    elif stype == 'SplitBodyFeature':
        lines.append(f'{I2}print("Step {idx}: {sname} (SplitBodyFeature) SKIPPED - not yet implemented")')

    elif stype == 'DraftFeature':
        lines.append(f'{I2}print("Step {idx}: {sname} (DraftFeature) SKIPPED - not yet implemented")')

    elif stype == 'RectangularPatternFeature':
        qty1 = step.get('quantity_one', 2)
        dist1 = step.get('distance_one', 1.0)
        dir1 = step.get('direction_one')
        input_indices = step.get('input_timeline_indices', [])
        if input_indices:
            prev_feats = [context.feature_vars[fi] for fi in input_indices if fi in context.feature_vars]
        else:
            all_fi = sorted(fi for fi in context.feature_vars if fi < idx)
            prev_feats = [context.feature_vars[all_fi[-1]]] if all_fi else []

        if prev_feats and dir1:
            if abs(dir1[0]) > 0.9:
                axis_expr = f"{comp_var}.xConstructionAxis"
            elif abs(dir1[1]) > 0.9:
                axis_expr = f"{comp_var}.yConstructionAxis"
            else:
                axis_expr = f"{comp_var}.zConstructionAxis"
            lines.append(f"{I2}_pc = adsk.core.ObjectCollection.create()")
            for fv in prev_feats:
                lines.append(f"{I2}if {fv}: _pc.add({fv})")
            lines.append(f"{I2}if _pc.count > 0:")
            lines.append(f"{I2}    _rpi = {comp_var}.features.rectangularPatternFeatures.createInput(_pc, {axis_expr})")
            lines.append(f"{I2}    _rpi.quantityOne = VI({qty1})")
            lines.append(f"{I2}    _rpi.distanceOne = VI({dist1})")
            qty2 = step.get('quantity_two')
            if qty2 and qty2 > 1:
                dir2 = step.get('direction_two')
                if dir2:
                    if abs(dir2[0]) > 0.9:
                        axis2 = f"{comp_var}.xConstructionAxis"
                    elif abs(dir2[1]) > 0.9:
                        axis2 = f"{comp_var}.yConstructionAxis"
                    else:
                        axis2 = f"{comp_var}.zConstructionAxis"
                    lines.append(f"{I2}    _rpi.setDirectionTwo({axis2}, VI({qty2}), VI({step.get('distance_two', 1.0)}))")
            lines.append(f"{I2}    {comp_var}.features.rectangularPatternFeatures.add(_rpi)")
            lines.append(f'{I2}    print("Step {idx}: {sname} x{qty1}")')
            lines.append(f"{I2}else:")
            lines.append(f'{I2}    print("Step {idx}: {sname} SKIPPED - no features")')
        else:
            lines.append(f'{I2}print("Step {idx}: {sname} SKIPPED - no data")')

    else:
        lines.append(f'{I2}print("Step {idx}: {sname} ({stype}) SKIPPED")')


# ---------------------------------------------------------------------------
#  Full reconstruction script — delegates to generate_single_step
# ---------------------------------------------------------------------------

def _generate_reconstruction_script(data: dict) -> str:
    """Generate a full Fusion 360 Python reconstruction script by calling
    generate_single_step for each timeline step. Single source of truth."""
    timeline = data.get('timeline', [])
    ctx = ReconstructionContext()

    full_code = None
    for i, step in enumerate(timeline):
        code = generate_single_step(step, ctx, timeline)
        if i == 0:
            full_code = code
        else:
            # Extract just the step block (after helpers)
            lines = code.split('\n')
            for li, line in enumerate(lines):
                if line.strip().startswith('# ── Step'):
                    full_code += '\n' + '\n'.join(lines[li:])
                    break

    # Append visual properties restoration
    visual = data.get('visual')
    if visual and full_code:
        full_code += '\n' + _generate_visual_code(visual)

    return full_code or ''


def _generate_visual_code(visual: dict) -> str:
    """Generate code to restore appearances, materials, opacity, visibility."""
    lines = []
    lines.append("# ── Visual Properties ──")
    lines.append("try:")
    I = "    "

    # Helper: find appearance by name, searching design then libraries
    lines.append(f"{I}def _find_appearance(name):")
    lines.append(f"{I}    try:")
    lines.append(f"{I}        a = design.appearances.itemByName(name)")
    lines.append(f"{I}        if a: return a")
    lines.append(f"{I}    except: pass")
    lines.append(f"{I}    for li in range(app.materialLibraries.count):")
    lines.append(f"{I}        try:")
    lines.append(f"{I}            a = app.materialLibraries.item(li).appearances.itemByName(name)")
    lines.append(f"{I}            if a: return a")
    lines.append(f"{I}        except: pass")
    lines.append(f"{I}    return None")

    # Helper: find material by name
    lines.append(f"{I}def _find_material(name):")
    lines.append(f"{I}    for li in range(app.materialLibraries.count):")
    lines.append(f"{I}        try:")
    lines.append(f"{I}            m = app.materialLibraries.item(li).materials.itemByName(name)")
    lines.append(f"{I}            if m: return m")
    lines.append(f"{I}        except: pass")
    lines.append(f"{I}    return None")

    # Apply body-level properties — match by volume + bbox (reliable across reconstructions)
    bodies = visual.get('bodies', [])
    if bodies:
        lines.append(f"{I}# Body appearances, materials, opacity, visibility")
        lines.append(f"{I}def _find_body_by_volume(comp, vol, bb_min=None, bb_max=None):")
        lines.append(f"{I}    best, best_d = None, 1e9")
        lines.append(f"{I}    for _bi in range(comp.bRepBodies.count):")
        lines.append(f"{I}        _b = comp.bRepBodies.item(_bi)")
        lines.append(f"{I}        try:")
        lines.append(f"{I}            _d = abs(_b.physicalProperties.volume - vol)")
        lines.append(f"{I}            if bb_min:")
        lines.append(f"{I}                _bb = _b.boundingBox")
        lines.append(f"{I}                _d += abs(_bb.minPoint.x-bb_min[0]) + abs(_bb.minPoint.y-bb_min[1]) + abs(_bb.minPoint.z-bb_min[2])")
        lines.append(f"{I}                _d += abs(_bb.maxPoint.x-bb_max[0]) + abs(_bb.maxPoint.y-bb_max[1]) + abs(_bb.maxPoint.z-bb_max[2])")
        lines.append(f"{I}            if _d < best_d: best, best_d = _b, _d")
        lines.append(f"{I}        except: pass")
        lines.append(f"{I}    return best")

        for bd in bodies:
            comp = bd.get('component', '')
            vol = bd.get('volume', 0)
            bb_min = bd.get('bb_min')
            bb_max = bd.get('bb_max')
            if comp:
                lines.append(f"{I}_comp = None")
                lines.append(f"{I}for _oi in range(rootComp.occurrences.count):")
                lines.append(f"{I}    if rootComp.occurrences.item(_oi).component.name == '{comp}':")
                lines.append(f"{I}        _comp = rootComp.occurrences.item(_oi).component; break")
                lines.append(f"{I}_b = _find_body_by_volume(_comp, {vol}, {bb_min}, {bb_max}) if _comp else None")
            else:
                lines.append(f"{I}_b = _find_body_by_volume(rootComp, {vol}, {bb_min}, {bb_max})")

            lines.append(f"{I}if _b:")
            if 'appearance' in bd:
                lines.append(f"{I}    try:")
                lines.append(f"{I}        _a = _find_appearance('{bd['appearance']}')")
                lines.append(f"{I}        if _a: _b.appearance = _a")
                lines.append(f"{I}    except: pass")
            if 'material' in bd:
                lines.append(f"{I}    try:")
                lines.append(f"{I}        _m = _find_material('{bd['material']}')")
                lines.append(f"{I}        if _m: _b.material = _m")
                lines.append(f"{I}    except: pass")
            if 'opacity' in bd:
                lines.append(f"{I}    _b.opacity = {bd['opacity']}")
            if bd.get('visible') is False:
                lines.append(f"{I}    _b.isVisible = False")

    # Apply per-face overrides
    face_overrides = visual.get('face_overrides', [])
    if face_overrides:
        lines.append(f"{I}# Per-face appearance overrides")
        # Build body volume lookup from bodies section
        body_volumes = {}
        for bd in bodies:
            bkey = (bd.get('component', ''), bd['name'])
            body_volumes[bkey] = (bd.get('volume', 0), bd.get('bb_min'), bd.get('bb_max'))
        # Group by body
        by_body = {}
        for fo in face_overrides:
            bkey = (fo.get('component', ''), fo['body'])
            if bkey not in by_body:
                by_body[bkey] = []
            by_body[bkey].append(fo)

        for (comp, bname), overrides in by_body.items():
            vol, bb_min, bb_max = body_volumes.get((comp, bname), (0, None, None))
            if comp:
                lines.append(f"{I}_comp = None")
                lines.append(f"{I}for _oi in range(rootComp.occurrences.count):")
                lines.append(f"{I}    if rootComp.occurrences.item(_oi).component.name == '{comp}':")
                lines.append(f"{I}        _comp = rootComp.occurrences.item(_oi).component; break")
                lines.append(f"{I}_b = _find_body_by_volume(_comp, {vol}, {bb_min}, {bb_max}) if _comp else None")
            else:
                lines.append(f"{I}_b = _find_body_by_volume(rootComp, {vol}, {bb_min}, {bb_max})")
            lines.append(f"{I}if _b:")
            for fo in overrides:
                area = fo['face_area']
                bb = fo['face_bb_center']
                app_name = fo['appearance']
                lines.append(f"{I}    _fa = _find_appearance('{app_name}')")
                lines.append(f"{I}    if _fa:")
                lines.append(f"{I}        for _fi in range(_b.faces.count):")
                lines.append(f"{I}            _f = _b.faces.item(_fi)")
                lines.append(f"{I}            if abs(_f.area - {area}) < 0.001:")
                lines.append(f"{I}                _fbb = _f.boundingBox")
                lines.append(f"{I}                _cx = round((_fbb.minPoint.x+_fbb.maxPoint.x)/2, 3)")
                lines.append(f"{I}                _cy = round((_fbb.minPoint.y+_fbb.maxPoint.y)/2, 3)")
                lines.append(f"{I}                _cz = round((_fbb.minPoint.z+_fbb.maxPoint.z)/2, 3)")
                lines.append(f"{I}                if abs(_cx-{bb[0]})<0.01 and abs(_cy-{bb[1]})<0.01 and abs(_cz-{bb[2]})<0.01:")
                lines.append(f"{I}                    _f.appearance = _fa")

    # Apply occurrence visibility
    occs = visual.get('occurrences', [])
    for ov in occs:
        comp_name = ov['component']
        if not ov.get('visible', True):
            lines.append(f"{I}for _oi in range(rootComp.occurrences.count):")
            lines.append(f"{I}    if rootComp.occurrences.item(_oi).component.name == '{comp_name}':")
            lines.append(f"{I}        rootComp.occurrences.item(_oi).isLightBulbOn = False")
        if 'appearance' in ov:
            lines.append(f"{I}for _oi in range(rootComp.occurrences.count):")
            lines.append(f"{I}    if rootComp.occurrences.item(_oi).component.name == '{comp_name}':")
            lines.append(f"{I}        _oa = _find_appearance('{ov['appearance']}')")
            lines.append(f"{I}        if _oa: rootComp.occurrences.item(_oi).appearance = _oa")

    # Ensure root component is active (sub-component context causes ghosting)
    lines.append(f"{I}design.activateRootComponent()")
    lines.append(f'{I}print("Visual properties applied")')
    lines.append("except Exception as _ve:")
    lines.append(f'    print(f"Visual properties error: {{_ve}}")')
    lines.append(f'    try: design.activateRootComponent()')
    lines.append(f'    except: pass')

    return '\n'.join(lines)
