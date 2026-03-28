"""
Fusion 360 design reconstruction code generator.

Provides:
- _generate_reconstruction_script(data) — generates full monolithic script (legacy)
- ReconstructionContext — tracks state across per-step generation
- generate_single_step(step, context, all_steps) — generates code for one step
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

_OP = {0: adsk.fusion.FeatureOperations.JoinFeatureOperation,
       1: adsk.fusion.FeatureOperations.CutFeatureOperation,
       2: adsk.fusion.FeatureOperations.IntersectFeatureOperation,
       3: adsk.fusion.FeatureOperations.NewBodyFeatureOperation}

def do_extrude(comp, profile, distance, operation, symmetric=False):
    ext_input = comp.features.extrudeFeatures.createInput(profile, _OP[operation])
    if symmetric:
        ext_input.setSymmetricExtent(VI(abs(distance)), True)
    else:
        ext_input.setDistanceExtent(False, VI(distance))
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
            ok, pt = body.edges.item(ei).evaluator.getPointAtParameter(0.5)
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
        return False
    try:
        if "normal" in desc and hasattr(g, 'normal'):
            dn = desc["normal"]
            if not (abs(g.normal.x - dn[0]) < tol and
                    abs(g.normal.y - dn[1]) < tol and
                    abs(g.normal.z - dn[2]) < tol):
                return False
        elif "radius" in desc and hasattr(g, 'radius'):
            if abs(g.radius - desc["radius"]) > tol:
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

def find_edges_by_descriptors(comp, descriptors):
    coll = adsk.core.ObjectCollection.create()
    for desc in descriptors:
        center = desc["center"]
        fa_desc = desc["face_a"]
        fb_desc = desc["face_b"]
        best_edge, best_score = None, 1e9
        for bi in range(comp.bRepBodies.count):
            body = comp.bRepBodies.item(bi)
            for ei in range(body.edges.count):
                edge = body.edges.item(ei)
                try:
                    if edge.faces.count < 2:
                        continue
                    f0 = edge.faces.item(0)
                    f1 = edge.faces.item(1)
                    match = ((_face_matches_desc(f0, fa_desc) and _face_matches_desc(f1, fb_desc)) or
                             (_face_matches_desc(f0, fb_desc) and _face_matches_desc(f1, fa_desc)))
                    if match:
                        # Score by edge midpoint proximity to descriptor center
                        try:
                            _ok, _ept = edge.evaluator.getPointAtParameter(0.5)
                            if not _ok: raise Exception()
                        except:
                            _ebb = edge.boundingBox
                            _ept = type('P', (), {'x': (_ebb.minPoint.x+_ebb.maxPoint.x)/2, 'y': (_ebb.minPoint.y+_ebb.maxPoint.y)/2, 'z': (_ebb.minPoint.z+_ebb.maxPoint.z)/2})()
                        score = math.sqrt((_ept.x - center[0])**2 + (_ept.y - center[1])**2 + (_ept.z - center[2])**2)
                        if score < best_score:
                            best_edge = edge
                            best_score = score
                except:
                    pass
        if best_edge:
            coll.add(best_edge)
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
        self.plane_vars: dict[int, str] = {}         # step_index -> var
        self.feature_vars: dict[int, str] = {}       # step_index -> var
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
            "plane_vars": {str(k): v for k, v in self.plane_vars.items()},
            "feature_vars": {str(k): v for k, v in self.feature_vars.items()},
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
        ctx.plane_vars = {int(k): v for k, v in d.get("plane_vars", {}).items()}
        ctx.feature_vars = {int(k): v for k, v in d.get("feature_vars", {}).items()}
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

    # Determine active component
    if body_name and body_name in context.comp_names:
        context.comp_var = context.comp_names[body_name]
    elif body_name and body_name not in context.comp_names and context.comp_var != "rootComp":
        context.comp_var = "rootComp"

    comp_var = context.comp_var
    I = ""       # no indent — top level in single step
    I2 = "    "  # single indent

    lines = [get_helpers_code(), "", f"# ── Step {idx}: {sname} ──"]

    # Preamble: look up previously created sketches/planes by name
    # (needed because each exec() has fresh globals)
    preamble_lines = []
    for sk_name, sk_var in context.sketch_vars.items():
        preamble_lines.append(f'{sk_var} = None')
        preamble_lines.append(f'for _i in range(rootComp.sketches.count):')
        preamble_lines.append(f'    if rootComp.sketches.item(_i).name == "{sk_name}":')
        preamble_lines.append(f'        {sk_var} = rootComp.sketches.item(_i); break')
    for pidx, pvar in context.plane_vars.items():
        plane_name = None
        for s in all_steps:
            if s.get('index') == pidx and s.get('type') == 'ConstructionPlane':
                plane_name = s.get('name')
                break
        if plane_name:
            preamble_lines.append(f'{pvar} = None')
            preamble_lines.append(f'for _i in range(rootComp.constructionPlanes.count):')
            preamble_lines.append(f'    if rootComp.constructionPlanes.item(_i).name == "{plane_name}":')
            preamble_lines.append(f'        {pvar} = rootComp.constructionPlanes.item(_i); break')

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


def _generate_step_body(step, context, all_steps, lines, I2, comp_var):
    """Generate the body of a single step (shared between single-step and full-script modes)."""
    idx = step['index']
    stype = step.get('type', '')
    sname = step.get('name', f'step_{idx}')
    body_name = step.get('body_name', '')

    if stype == 'Sketch':
        var = f"sk_{idx}"
        context.sketch_vars[sname] = var
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
            lines.append(f"{I2}_face = None")
            lines.append(f"{I2}for _bi in range({comp_var}.bRepBodies.count):")
            lines.append(f"{I2}    _b = {comp_var}.bRepBodies.item(_bi)")
            lines.append(f"{I2}    for _fi in range(_b.faces.count):")
            lines.append(f"{I2}        try:")
            lines.append(f"{I2}            _g = _b.faces.item(_fi).geometry")
            lines.append(f"{I2}            if hasattr(_g,'normal') and abs(_g.normal.z-({fnz}))<0.15:")
            lines.append(f"{I2}                _zc=(_b.faces.item(_fi).boundingBox.minPoint.z+_b.faces.item(_fi).boundingBox.maxPoint.z)/2")
            lines.append(f"{I2}                if abs(_zc-{fz})<0.15: _face=_b.faces.item(_fi); break")
            lines.append(f"{I2}        except: pass")
            lines.append(f"{I2}    if _face: break")
            lines.append(f"{I2}if _face:")
            lines.append(f"{I2}    {var} = {comp_var}.sketches.add(_face)")
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
                    make_construction = False
                    if sname in context.revolve_profile_centroids and not ic:
                        centroids = context.revolve_profile_centroids[sname]
                        near_any = False
                        for pcx, pcy in centroids:
                            d1 = _math.sqrt((sx-pcx)**2 + (sy-pcy)**2)
                            d2 = _math.sqrt((ex-pcx)**2 + (ey-pcy)**2)
                            if d1 < 0.25 and d2 < 0.25:
                                near_any = True; break
                        if not near_any:
                            make_construction = True
                    if ic or make_construction:
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
                    if ic:
                        lines.append(f"{I2}_cl={var}.sketchCurves.sketchArcs.addByCenterStartSweep(P({cx},{cy}),P({sx},{sy}),{round(sweep,6)}); _cl.isConstruction=True")
                    else:
                        lines.append(f"{I2}{var}.sketchCurves.sketchArcs.addByCenterStartSweep(P({cx},{cy}),P({sx},{sy}),{round(sweep,6)})")
            lines.append(f"{I2}{var}.isComputeDeferred = False")
        lines.append(f'{I2}print(f"Step {idx}: {sname} - {{{var}.profiles.count}} profiles")')

    elif stype == 'ExtrudeFeature':
        var = f"ext_{idx}"
        context.feature_vars[idx] = var
        lines.append(f"{I2}{var} = None")
        sk_name = step.get('sketch_name', '')
        sk_var = context.sketch_vars.get(sk_name, 'sk_0')
        profiles = step.get('profiles', [])
        op = step.get('operation', 0)
        dist = step.get('distance', 0)
        is_sym = 'Symmetric' in step.get('extent_type', '')

        if op == 1 and 0 < abs(dist) < 0.005:
            dist = -0.005 if dist < 0 else 0.005

        body_z_min = step.get('body_z_min')
        body_z_max = step.get('body_z_max')
        sk_z = context.sketch_z.get(sk_name, 0)
        flipped = context.sketch_normal_flipped.get(sk_name, False)
        if op == 3 and not is_sym:
            if body_z_min is not None and body_z_max is not None:
                body_mid = (body_z_min + body_z_max) / 2
                if body_mid > sk_z:
                    dist = abs(dist)
                else:
                    dist = -abs(dist)
            else:
                goes_up = step.get('extrude_goes_up')
                if goes_up is not None:
                    if (goes_up and dist < 0) or (not goes_up and dist > 0):
                        dist = -dist
        elif flipped and op in (0, 1):
            goes_up = step.get('extrude_goes_up')
            if goes_up is not None:
                if (goes_up and dist < 0) or (not goes_up and dist > 0):
                    dist = -dist

        fn = "do_extrude" if op == 3 else "extrude_safe"
        sym = ", symmetric=True" if is_sym else ""
        cx_sign = -1 if context.sketch_normal_flipped.get(sk_name, False) else 1

        if len(profiles) == 1:
            p = profiles[0]
            _cx = round(p['centroid'][0] * cx_sign, 4)
            _cy = p['centroid'][1]
            lines.append(f"{I2}_p = find_profile({sk_var}, {p['area']}, {_cx}, {_cy})")
            lines.append(f"{I2}if not _p: _p = find_profile({sk_var}, {p['area']}, {_cx}, {_cy}, tol_a=0.3, tol_p=0.5)")
            lines.append(f"{I2}if not _p: _p = find_profile({sk_var}, {p['area']}, {_cx}, {_cy}, centroid_only=True)")
            lines.append(f"{I2}if _p:")
            lines.append(f'{I2}    {var} = {fn}({comp_var}, _p, {dist}, {op}{sym})')
            lines.append(f'{I2}    print("Step {idx}: {sname}")')
            lines.append(f"{I2}else:")
            lines.append(f'{I2}    print("Step {idx}: {sname} FAILED")')
        elif len(profiles) > 1:
            targets = [(p['area'], round(p['centroid'][0] * cx_sign, 4), p['centroid'][1]) for p in profiles]
            lines.append(f"{I2}_profs = find_profiles({sk_var}, {targets})")
            lines.append(f"{I2}if _profs.count > 0:")
            lines.append(f'{I2}    {var} = {fn}({comp_var}, _profs, {dist}, {op}{sym})')
            lines.append(f'{I2}    print(f"Step {idx}: {sname} - {{_profs.count}}/{len(profiles)}")')
            lines.append(f"{I2}else:")
            lines.append(f'{I2}    print("Step {idx}: {sname} FAILED")')
        else:
            lines.append(f'{I2}print("Step {idx}: {sname} SKIPPED - no profiles")')

    elif stype == 'ConstructionPlane':
        var = f"plane_{idx}"
        context.plane_vars[idx] = var
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
        context.comp_names[cname] = "rootComp"
        context.comp_var = "rootComp"
        lines.append(f'{I2}print("Step {idx}: {cname} (bodies in rootComp)")')

    elif stype == 'FilletFeature':
        edges_data = step.get('edge_sets', [])
        faces = step.get('faces', [])
        if edges_data and faces:
            r = edges_data[0].get('radius', 0.1)
            bboxes = [(f['bb_min'], f['bb_max']) for f in faces if 'bb_min' in f]
            if bboxes:
                # Generate inline edge matching code using fillet face BB
                # Strategy: for each fillet face BB, find edge with matching orientation
                # (span along dominant axis) and closest midpoint, avoiding duplicates
                lines.append(f"{I2}_body = get_body({comp_var}, '{body_name}') or {comp_var}.bRepBodies.item(0)")
                lines.append(f"{I2}_edges = adsk.core.ObjectCollection.create()")
                lines.append(f"{I2}_used_edges = set()")
                lines.append(f"{I2}_fillet_bbs = {bboxes}")
                lines.append(f"{I2}for _fbb_min, _fbb_max in _fillet_bbs:")
                lines.append(f"{I2}    _fc = [(_fbb_min[i]+_fbb_max[i])/2 for i in range(3)]")
                lines.append(f"{I2}    _fspan = [_fbb_max[i]-_fbb_min[i] for i in range(3)]")
                lines.append(f"{I2}    _dom = _fspan.index(max(_fspan))")  # dominant axis
                lines.append(f"{I2}    _flen = _fspan[_dom]")
                lines.append(f"{I2}    _best_e, _best_d = None, 1e9")
                lines.append(f"{I2}    for _ei in range(_body.edges.count):")
                lines.append(f"{I2}        if _ei in _used_edges: continue")
                lines.append(f"{I2}        _ebb = _body.edges.item(_ei).boundingBox")
                lines.append(f"{I2}        _espan = [_ebb.maxPoint.x-_ebb.minPoint.x, _ebb.maxPoint.y-_ebb.minPoint.y, _ebb.maxPoint.z-_ebb.minPoint.z]")
                lines.append(f"{I2}        _e_dom = _espan.index(max(_espan))")
                lines.append(f"{I2}        if _e_dom != _dom: continue")  # must have same orientation
                lines.append(f"{I2}        if abs(_espan[_dom] - _flen) > 0.3: continue")  # similar length
                lines.append(f"{I2}        try:")
                lines.append(f"{I2}            _ok, _pt = _body.edges.item(_ei).evaluator.getPointAtParameter(0.5)")
                lines.append(f"{I2}            if not _ok: raise Exception()")
                lines.append(f"{I2}            _mx, _my, _mz = _pt.x, _pt.y, _pt.z")
                lines.append(f"{I2}        except:")
                lines.append(f"{I2}            _mx = (_ebb.minPoint.x+_ebb.maxPoint.x)/2")
                lines.append(f"{I2}            _my = (_ebb.minPoint.y+_ebb.maxPoint.y)/2")
                lines.append(f"{I2}            _mz = (_ebb.minPoint.z+_ebb.maxPoint.z)/2")
                lines.append(f"{I2}        _d = math.sqrt((_mx-_fc[0])**2+(_my-_fc[1])**2+(_mz-_fc[2])**2)")
                lines.append(f"{I2}        if _d < _best_d: _best_e, _best_d = _ei, _d")
                lines.append(f"{I2}    if _best_e is not None:")
                lines.append(f"{I2}        _edges.add(_body.edges.item(_best_e))")
                lines.append(f"{I2}        _used_edges.add(_best_e)")
            else:
                lines.append(f"{I2}_edges = adsk.core.ObjectCollection.create()")
            lines.append(f"{I2}if _edges.count > 0:")
            lines.append(f"{I2}    _fi={comp_var}.features.filletFeatures.createInput()")
            lines.append(f"{I2}    _fi.addConstantRadiusEdgeSet(_edges, VI({r}), True)")
            lines.append(f"{I2}    {comp_var}.features.filletFeatures.add(_fi)")
            lines.append(f'{I2}    print(f"Step {idx}: {sname} r={round(r*10,1)}mm - {{_edges.count}} edges")')
            lines.append(f"{I2}else:")
            lines.append(f'{I2}    print("Step {idx}: {sname} SKIPPED - no edges found")')
        else:
            lines.append(f'{I2}print("Step {idx}: {sname} SKIPPED - no data")')

    elif stype == 'ChamferFeature':
        edges_data = step.get('edge_sets', [])
        faces = step.get('faces', [])
        if edges_data and faces:
            d = edges_data[0].get('distance', 0.1)
            bboxes = [(f['bb_min'], f['bb_max']) for f in faces if 'bb_min' in f]
            if bboxes:
                lines.append(f"{I2}_body = get_body({comp_var}, '{body_name}') or {comp_var}.bRepBodies.item(0)")
                lines.append(f"{I2}_edges = adsk.core.ObjectCollection.create()")
                lines.append(f"{I2}_used_edges = set()")
                lines.append(f"{I2}_fillet_bbs = {bboxes}")
                lines.append(f"{I2}for _fbb_min, _fbb_max in _fillet_bbs:")
                lines.append(f"{I2}    _fc = [(_fbb_min[i]+_fbb_max[i])/2 for i in range(3)]")
                lines.append(f"{I2}    _fspan = [_fbb_max[i]-_fbb_min[i] for i in range(3)]")
                lines.append(f"{I2}    _dom = _fspan.index(max(_fspan))")
                lines.append(f"{I2}    _flen = _fspan[_dom]")
                lines.append(f"{I2}    _best_e, _best_d = None, 1e9")
                lines.append(f"{I2}    for _ei in range(_body.edges.count):")
                lines.append(f"{I2}        if _ei in _used_edges: continue")
                lines.append(f"{I2}        _ebb = _body.edges.item(_ei).boundingBox")
                lines.append(f"{I2}        _espan = [_ebb.maxPoint.x-_ebb.minPoint.x, _ebb.maxPoint.y-_ebb.minPoint.y, _ebb.maxPoint.z-_ebb.minPoint.z]")
                lines.append(f"{I2}        _e_dom = _espan.index(max(_espan))")
                lines.append(f"{I2}        if _e_dom != _dom: continue")
                lines.append(f"{I2}        if abs(_espan[_dom] - _flen) > 0.3: continue")
                lines.append(f"{I2}        try:")
                lines.append(f"{I2}            _ok, _pt = _body.edges.item(_ei).evaluator.getPointAtParameter(0.5)")
                lines.append(f"{I2}            if not _ok: raise Exception()")
                lines.append(f"{I2}            _mx, _my, _mz = _pt.x, _pt.y, _pt.z")
                lines.append(f"{I2}        except:")
                lines.append(f"{I2}            _mx = (_ebb.minPoint.x+_ebb.maxPoint.x)/2")
                lines.append(f"{I2}            _my = (_ebb.minPoint.y+_ebb.maxPoint.y)/2")
                lines.append(f"{I2}            _mz = (_ebb.minPoint.z+_ebb.maxPoint.z)/2")
                lines.append(f"{I2}        _d = math.sqrt((_mx-_fc[0])**2+(_my-_fc[1])**2+(_mz-_fc[2])**2)")
                lines.append(f"{I2}        if _d < _best_d: _best_e, _best_d = _ei, _d")
                lines.append(f"{I2}    if _best_e is not None:")
                lines.append(f"{I2}        _edges.add(_body.edges.item(_best_e))")
                lines.append(f"{I2}        _used_edges.add(_best_e)")
            else:
                lines.append(f"{I2}_edges = adsk.core.ObjectCollection.create()")
            lines.append(f"{I2}if _edges.count > 0:")
            lines.append(f"{I2}    _chi={comp_var}.features.chamferFeatures.createInput2()")
            lines.append(f"{I2}    _chi.chamferEdgeSets.addEqualDistanceChamferEdgeSet(_edges, VI({d}), True)")
            lines.append(f"{I2}    {comp_var}.features.chamferFeatures.add(_chi)")
            lines.append(f'{I2}    print(f"Step {idx}: {sname} - {{_edges.count}} edges")')
            lines.append(f"{I2}else:")
            lines.append(f'{I2}    print("Step {idx}: {sname} SKIPPED - no edges found")')
        else:
            lines.append(f'{I2}print("Step {idx}: {sname} SKIPPED - no data")')

    elif stype == 'RevolveFeature':
        var = f"rev_{idx}"
        context.feature_vars[idx] = var
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
            if prof:
                a = prof.get('area', 0)
                cx, cy = prof.get('centroid', [0,0])
                lines.append(f"{I2}_p = find_profile({prev_sk}, {a}, {cx}, {cy}, tol_a=0.3, tol_p=0.3)")
            else:
                lines.append(f"{I2}_p = {prev_sk}.profiles.item(0)")

            if axis_expr:
                lines.append(f"{I2}_ri = {comp_var}.features.revolveFeatures.createInput(_p, {axis_expr}, adsk.fusion.FeatureOperations.{op_name})")
                lines.append(f"{I2}_ri.setAngleExtent(False, VI({angle_rad}))")
                lines.append(f"{I2}{var} = {comp_var}.features.revolveFeatures.add(_ri)")
                lines.append(f'{I2}print("Step {idx}: {sname}")')
            else:
                lines.append(f"{I2}for _ax in [{comp_var}.zConstructionAxis, {comp_var}.yConstructionAxis, {comp_var}.xConstructionAxis]:")
                lines.append(f"{I2}    try:")
                lines.append(f"{I2}        _ri = {comp_var}.features.revolveFeatures.createInput(_p, _ax, adsk.fusion.FeatureOperations.{op_name})")
                lines.append(f"{I2}        _ri.setAngleExtent(False, VI({angle_rad}))")
                lines.append(f"{I2}        {var} = {comp_var}.features.revolveFeatures.add(_ri)")
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

        # Collect recent features to pattern
        all_feats = [(fi, context.feature_vars[fi]) for fi in sorted(context.feature_vars) if fi < idx and fi > idx - 5]
        rev_feats = [fv for fi, fv in all_feats if fv.startswith('rev_')]
        prev_feats = rev_feats if rev_feats else [fv for _, fv in all_feats[-3:]]

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

    else:
        lines.append(f'{I2}print("Step {idx}: {sname} ({stype}) SKIPPED")')


# ---------------------------------------------------------------------------
#  Legacy: full monolithic reconstruction script
# ---------------------------------------------------------------------------

def _generate_reconstruction_script(data: dict) -> str:
    """Generate a Fusion 360 Python reconstruction script from exported timeline data."""

    HELPERS_LEGACY = '''import adsk.core, adsk.fusion, math, traceback

def P(x, y, z=0):
    return adsk.core.Point3D.create(x, y, z)

def VI(v):
    return adsk.core.ValueInput.createByReal(v)

def find_profile(sketch, area, cx, cy, tol_a=0.15, tol_p=0.2, centroid_only=False):
    best, best_s = None, 1e9
    # Also track best centroid-only match as fallback
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
            # Track closest by position (for centroid_only fallback)
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

_OP = {0: adsk.fusion.FeatureOperations.JoinFeatureOperation,
       1: adsk.fusion.FeatureOperations.CutFeatureOperation,
       2: adsk.fusion.FeatureOperations.IntersectFeatureOperation,
       3: adsk.fusion.FeatureOperations.NewBodyFeatureOperation}

def do_extrude(comp, profile, distance, operation, symmetric=False):
    ext_input = comp.features.extrudeFeatures.createInput(profile, _OP[operation])
    if symmetric:
        ext_input.setSymmetricExtent(VI(abs(distance)), True)
    else:
        ext_input.setDistanceExtent(False, VI(distance))
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
                    # Last resort for cuts: try AllExtent
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
    """Find the body most likely to contain edges matching the face bounding boxes."""
    if not face_bboxes:
        return comp.bRepBodies.item(0) if comp.bRepBodies.count > 0 else None
    # Compute target center from face BBs
    tcx = sum((bb[0][0]+bb[1][0])/2 for bb in face_bboxes) / len(face_bboxes)
    tcy = sum((bb[0][1]+bb[1][1])/2 for bb in face_bboxes) / len(face_bboxes)
    tcz = sum((bb[0][2]+bb[1][2])/2 for bb in face_bboxes) / len(face_bboxes)
    best_body, best_score = None, 1e9
    for bi in range(comp.bRepBodies.count):
        body = comp.bRepBodies.item(bi)
        bb = body.boundingBox
        # Check if target center is within or near body BB
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
    """Find edges nearest to the Z-zone defined by fillet/chamfer face bounding boxes.
    Uses the center of all face BBs as target zone, then finds closest edges."""
    if not face_bboxes:
        return adsk.core.ObjectCollection.create()
    # Compute target zone from face BBs
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
    # Score all edges: prefer edges near the Z-zone
    scored = []
    for ei in range(body.edges.count):
        try:
            ok, pt = body.edges.item(ei).evaluator.getPointAtParameter(0.5)
            if ok and abs(pt.z - tz) < z_tol:
                # Score by distance to nearest face BB center
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

def auto_combine(comp, target_z_min=None, target_z_max=None):
    """Combine a body touching the target Z zone with the largest body.
    If target_z given, only combine bodies that overlap that Z range."""
    if comp.bRepBodies.count <= 1:
        return
    # Find largest body by face count
    best_bi, best_fc = 0, 0
    for bi in range(comp.bRepBodies.count):
        fc = comp.bRepBodies.item(bi).faces.count
        if fc > best_fc:
            best_fc = fc
            best_bi = bi
    target = comp.bRepBodies.item(best_bi)
    tbb = target.boundingBox
    # Find body to combine: must overlap target Z zone AND touch the target body
    for bi in range(comp.bRepBodies.count):
        body = comp.bRepBodies.item(bi)
        if body == target:
            continue
        bb = body.boundingBox
        # Check if body overlaps target Z zone (if specified)
        if target_z_min is not None:
            if bb.maxPoint.z < target_z_min - 0.05 or bb.minPoint.z > target_z_max + 0.05:
                continue
        # Check if body touches target body (shared Z boundary within tolerance)
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
            return  # combined one body, done
        except:
            pass

def _face_matches_desc(face, desc, tol=0.15):
    """Check if a BRep face matches a geometric descriptor by type+properties.
    Returns True/False for geometry match (bb_center used separately for scoring)."""
    g = face.geometry
    gt = g.objectType.split("::")[-1] if g else ""
    dt = desc.get("type", "")
    if gt != dt:
        return False
    try:
        if "normal" in desc and hasattr(g, 'normal'):
            dn = desc["normal"]
            if not (abs(g.normal.x - dn[0]) < tol and
                    abs(g.normal.y - dn[1]) < tol and
                    abs(g.normal.z - dn[2]) < tol):
                return False
        elif "radius" in desc and hasattr(g, 'radius'):
            if abs(g.radius - desc["radius"]) > tol:
                return False
        return True
    except:
        pass
    return gt == dt

def _face_position_score(face, desc):
    """Score how close a face's BB center is to the descriptor's bb_center. Lower=better."""
    if "bb_center" not in desc:
        return 0
    try:
        bb = face.boundingBox
        fc = [(bb.minPoint.x+bb.maxPoint.x)/2, (bb.minPoint.y+bb.maxPoint.y)/2, (bb.minPoint.z+bb.maxPoint.z)/2]
        dc = desc["bb_center"]
        return math.sqrt((fc[0]-dc[0])**2 + (fc[1]-dc[1])**2 + (fc[2]-dc[2])**2)
    except:
        return 0

def find_edges_by_descriptors(comp, descriptors):
    """Find edges by matching adjacent face geometric descriptors.
    Each descriptor has center (approximate), face_a, face_b.
    Primary matching is by face types; center is used as tiebreaker."""
    coll = adsk.core.ObjectCollection.create()
    for desc in descriptors:
        center = desc["center"]
        fa_desc = desc["face_a"]
        fb_desc = desc["face_b"]
        best_edge, best_score = None, 1e9
        for bi in range(comp.bRepBodies.count):
            body = comp.bRepBodies.item(bi)
            for ei in range(body.edges.count):
                edge = body.edges.item(ei)
                try:
                    if edge.faces.count < 2:
                        continue
                    f0 = edge.faces.item(0)
                    f1 = edge.faces.item(1)
                    match = ((_face_matches_desc(f0, fa_desc) and _face_matches_desc(f1, fb_desc)) or
                             (_face_matches_desc(f0, fb_desc) and _face_matches_desc(f1, fa_desc)))
                    if match:
                        # Score by edge midpoint proximity to descriptor center
                        try:
                            _ok, _ept = edge.evaluator.getPointAtParameter(0.5)
                            if not _ok: raise Exception()
                        except:
                            _ebb = edge.boundingBox
                            _ept = type('P', (), {'x': (_ebb.minPoint.x+_ebb.maxPoint.x)/2, 'y': (_ebb.minPoint.y+_ebb.maxPoint.y)/2, 'z': (_ebb.minPoint.z+_ebb.maxPoint.z)/2})()
                        score = math.sqrt((_ept.x - center[0])**2 + (_ept.y - center[1])**2 + (_ept.z - center[2])**2)
                        if score < best_score:
                            best_edge = edge
                            best_score = score
                except:
                    pass
        if best_edge:
            coll.add(best_edge)
    return coll

'''

    lines = [HELPERS_LEGACY.strip(), "", "results = []", "try:"]
    I = "    "      # single indent (inside main try)
    I2 = "        "  # double indent (inside step try)

    sketch_vars = {}   # sketch_name -> var
    sketch_z = {}      # sketch_name -> z position (cm)
    sketch_normal_flipped = {}  # sketch_name -> True if plane normal is -Z
    plane_vars = {}    # step_index -> var
    comp_var = "rootComp"
    comp_names = {}    # component_name -> var
    feature_vars = {}  # step_index -> var
    in_component = False  # currently unused — body isolation too invasive

    # Pre-scan: find sketches used by revolves and their profile centroids
    revolve_sketches = set()
    revolve_profile_centroids = {}  # sketch_name -> [(cx, cy), ...]
    timeline = data.get('timeline', [])
    for si, step in enumerate(timeline):
        if step.get('type') == 'RevolveFeature':
            prof = step.get('profile', {})
            for pi in range(si - 1, -1, -1):
                if timeline[pi].get('type') == 'Sketch':
                    sk_name = timeline[pi].get('name', '')
                    revolve_sketches.add(sk_name)
                    if prof and 'centroid' in prof:
                        if sk_name not in revolve_profile_centroids:
                            revolve_profile_centroids[sk_name] = []
                        revolve_profile_centroids[sk_name].append(prof['centroid'])
                    break

    for step in timeline:
        idx = step['index']
        stype = step.get('type', '')
        sname = step.get('name', f'step_{idx}')

        lines.append(f"{I}# ── Step {idx}: {sname} ──")
        lines.append(f"{I}try:")

        # --- Determine active component ---
        body_name = step.get('body_name', '')
        if body_name and body_name in comp_names:
            comp_var = comp_names[body_name]
        elif body_name and body_name not in comp_names and comp_var != "rootComp":
            comp_var = "rootComp"

        if stype == 'Sketch':
            var = f"sk_{idx}"
            sketch_vars[sname] = var
            plane_ref = step.get('plane', 'XY')
            plane_origin = step.get('plane_origin')
            sketch_z[sname] = plane_origin[2] if plane_origin else 0
            # Track if sketch is on a flipped-normal construction plane
            # Only construction planes (not face/XY/XZ/YZ) need direction flip
            pn = step.get('plane_normal')
            is_construction_plane = plane_ref not in ('XY', 'XZ', 'YZ', 'face')
            sketch_normal_flipped[sname] = (is_construction_plane and pn is not None and pn[2] < -0.5)

            if plane_ref == 'XY':
                lines.append(f"{I2}{var} = {comp_var}.sketches.add({comp_var}.xYConstructionPlane)")
            elif plane_ref == 'XZ':
                lines.append(f"{I2}{var} = {comp_var}.sketches.add({comp_var}.xZConstructionPlane)")
            elif plane_ref == 'YZ':
                lines.append(f"{I2}{var} = {comp_var}.sketches.add({comp_var}.yZConstructionPlane)")
            elif plane_ref == 'face':
                fz = step.get('face_center_z', 0)
                fnz = step.get('face_normal', [0,0,1])[2]
                lines.append(f"{I2}_face = None")
                lines.append(f"{I2}for _bi in range({comp_var}.bRepBodies.count):")
                lines.append(f"{I2}    _b = {comp_var}.bRepBodies.item(_bi)")
                lines.append(f"{I2}    for _fi in range(_b.faces.count):")
                lines.append(f"{I2}        try:")
                lines.append(f"{I2}            _g = _b.faces.item(_fi).geometry")
                lines.append(f"{I2}            if hasattr(_g,'normal') and abs(_g.normal.z-({fnz}))<0.15:")
                lines.append(f"{I2}                _zc=(_b.faces.item(_fi).boundingBox.minPoint.z+_b.faces.item(_fi).boundingBox.maxPoint.z)/2")
                lines.append(f"{I2}                if abs(_zc-{fz})<0.15: _face=_b.faces.item(_fi); break")
                lines.append(f"{I2}        except: pass")
                lines.append(f"{I2}    if _face: break")
                lines.append(f"{I2}if _face:")
                lines.append(f"{I2}    {var} = {comp_var}.sketches.add(_face)")
                lines.append(f"{I2}else:")
                lines.append(f"{I2}    _pi={comp_var}.constructionPlanes.createInput()")
                lines.append(f"{I2}    _pi.setByOffset({comp_var}.xYConstructionPlane, VI({fz}))")
                lines.append(f"{I2}    {var}={comp_var}.sketches.add({comp_var}.constructionPlanes.add(_pi))")
            else:
                # Match plane by geometric position (not by name)
                plane_origin = step.get('plane_origin')
                found = None
                if plane_origin and plane_vars:
                    best_dist = 1e9
                    for pidx, pvar in plane_vars.items():
                        pstep = data['timeline'][pidx] if pidx < len(data['timeline']) else {}
                        # Use geometry_origin.z (signed) for matching
                        geo = pstep.get('geometry_origin')
                        p_z = geo[2] if geo else pstep.get('offset', 0)
                        d = abs(p_z - plane_origin[2])
                        if d < best_dist:
                            best_dist = d
                            found = f"plane_{pidx}"
                if not found:
                    # Fallback: match by name
                    for pidx, pvar in plane_vars.items():
                        pstep = data['timeline'][pidx] if pidx < len(data['timeline']) else {}
                        if pstep.get('name', '') == plane_ref:
                            found = f"plane_{pidx}"
                            break
                if found:
                    lines.append(f"{I2}{var} = {comp_var}.sketches.add({found})")
                else:
                    lines.append(f"{I2}# WARNING: plane '{plane_ref}' not resolved")
                    lines.append(f"{I2}{var} = {comp_var}.sketches.add({comp_var}.xYConstructionPlane)")

            lines.append(f'{I2}{var}.name = "{sname}"')

            curves = step.get('curves', [])
            # Mirror X for sketches on flipped-normal planes (-Z → +Z)
            mirror_x = sketch_normal_flipped.get(sname, False)
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
                        # For revolve sketches: only curves near profile centroid stay non-construction
                        make_construction = False
                        if sname in revolve_profile_centroids and not ic:
                            centroids = revolve_profile_centroids[sname]
                            near_any = False
                            for pcx, pcy in centroids:
                                d1 = _math.sqrt((sx-pcx)**2 + (sy-pcy)**2)
                                d2 = _math.sqrt((ex-pcx)**2 + (ey-pcy)**2)
                                if d1 < 0.25 and d2 < 0.25:
                                    near_any = True; break
                            if not near_any:
                                make_construction = True
                        if ic or make_construction:
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
                        if ic:
                            lines.append(f"{I2}_cl={var}.sketchCurves.sketchArcs.addByCenterStartSweep(P({cx},{cy}),P({sx},{sy}),{round(sweep,6)}); _cl.isConstruction=True")
                        else:
                            lines.append(f"{I2}{var}.sketchCurves.sketchArcs.addByCenterStartSweep(P({cx},{cy}),P({sx},{sy}),{round(sweep,6)})")
                lines.append(f"{I2}{var}.isComputeDeferred = False")
            lines.append(f'{I2}results.append(f"Step {idx}: {sname} - {{{var}.profiles.count}} profiles")')

        elif stype == 'ExtrudeFeature':
            var = f"ext_{idx}"
            feature_vars[idx] = var
            lines.append(f"{I2}{var} = None")
            sk_name = step.get('sketch_name', '')
            sk_var = sketch_vars.get(sk_name, 'sk_0')
            profile_indices = step.get('profile_indices', [])
            profiles = step.get('profiles', [])
            op = step.get('operation', 0)
            dist = step.get('distance', 0)
            is_sym = 'Symmetric' in step.get('extent_type', '')

            # Ensure minimum cut depth for very shallow engravings
            if op == 1 and 0 < abs(dist) < 0.005:
                dist = -0.005 if dist < 0 else 0.005

            # Direction correction using body z-range (geometric descriptor)
            body_z_min = step.get('body_z_min')
            body_z_max = step.get('body_z_max')
            sk_z = sketch_z.get(sk_name, 0)
            flipped = sketch_normal_flipped.get(sk_name, False)
            if op == 3 and not is_sym:
                # NewBody: use body z-range for deterministic direction
                if body_z_min is not None and body_z_max is not None:
                    body_mid = (body_z_min + body_z_max) / 2
                    if body_mid > sk_z:
                        dist = abs(dist)
                    else:
                        dist = -abs(dist)
                else:
                    # Fallback: goes_up heuristic
                    goes_up = step.get('extrude_goes_up')
                    if goes_up is not None:
                        if (goes_up and dist < 0) or (not goes_up and dist > 0):
                            dist = -dist
            elif flipped and op in (0, 1):
                # Join/Cut on flipped-normal construction plane:
                # Only flip if goes_up confirms the direction mismatch
                goes_up = step.get('extrude_goes_up')
                if goes_up is not None:
                    if (goes_up and dist < 0) or (not goes_up and dist > 0):
                        dist = -dist

            fn = "do_extrude" if op == 3 else "extrude_safe"
            sym = ", symmetric=True" if is_sym else ""

            # Mirror centroid X for flipped-normal sketches
            cx_sign = -1 if sketch_normal_flipped.get(sk_name, False) else 1

            if len(profiles) == 1:
                # Fallback: area/centroid matching
                p = profiles[0]
                _cx = round(p['centroid'][0] * cx_sign, 4)
                _cy = p['centroid'][1]
                lines.append(f"{I2}_p = find_profile({sk_var}, {p['area']}, {_cx}, {_cy})")
                lines.append(f"{I2}if not _p: _p = find_profile({sk_var}, {p['area']}, {_cx}, {_cy}, tol_a=0.3, tol_p=0.5)")
                lines.append(f"{I2}if not _p: _p = find_profile({sk_var}, {p['area']}, {_cx}, {_cy}, centroid_only=True)")
                lines.append(f"{I2}if _p:")
                lines.append(f'{I2}    {var} = {fn}({comp_var}, _p, {dist}, {op}{sym})')
                lines.append(f'{I2}    results.append("Step {idx}: {sname}")')
                lines.append(f"{I2}else:")
                lines.append(f'{I2}    results.append("Step {idx}: {sname} FAILED")')
            elif len(profiles) > 1:
                targets = [(p['area'], round(p['centroid'][0] * cx_sign, 4), p['centroid'][1]) for p in profiles]
                lines.append(f"{I2}_profs = find_profiles({sk_var}, {targets})")
                lines.append(f"{I2}if _profs.count > 0:")
                lines.append(f'{I2}    {var} = {fn}({comp_var}, _profs, {dist}, {op}{sym})')
                lines.append(f'{I2}    results.append(f"Step {idx}: {sname} - {{_profs.count}}/{len(profiles)}")')
                lines.append(f"{I2}else:")
                lines.append(f'{I2}    results.append("Step {idx}: {sname} FAILED")')
            else:
                lines.append(f'{I2}{var} = None')
                lines.append(f'{I2}results.append("Step {idx}: {sname} SKIPPED - no profiles")')

        elif stype == 'FilletFeature':
            edges_data = step.get('edge_sets', [])
            edge_descs = step.get('edge_descriptors', [])
            faces = step.get('faces', [])
            if edges_data and (edge_descs or faces):
                r = edges_data[0].get('radius', 0.1)
                bboxes = [(f['bb_min'], f['bb_max']) for f in faces if 'bb_min' in f]
                if edge_descs:
                    # PRIMARY: find edges by geometric descriptors
                    lines.append(f"{I2}_edges = find_edges_by_descriptors({comp_var}, {edge_descs})")
                    # FALLBACK: if descriptors found nothing, use face BB matching
                    if bboxes:
                        n_faces = len(bboxes)
                        lines.append(f"{I2}if _edges.count == 0:")
                        lines.append(f"{I2}    _body = get_body({comp_var}, '{body_name}') or find_body_for_edges({comp_var}, {bboxes}) or {comp_var}.bRepBodies.item(0)")
                        lines.append(f"{I2}    _edges = find_edges_by_zone(_body, {bboxes}, {n_faces})")
                elif bboxes:
                    n_faces = len(bboxes)
                    lines.append(f"{I2}_body = get_body({comp_var}, '{body_name}') or find_body_for_edges({comp_var}, {bboxes}) or {comp_var}.bRepBodies.item(0)")
                    lines.append(f"{I2}_edges = find_edges_by_zone(_body, {bboxes}, {n_faces})")
                else:
                    lines.append(f"{I2}_edges = adsk.core.ObjectCollection.create()")
                lines.append(f"{I2}if _edges.count > 0:")
                lines.append(f"{I2}    _fi={comp_var}.features.filletFeatures.createInput()")
                lines.append(f"{I2}    _fi.addConstantRadiusEdgeSet(_edges, VI({r}), True)")
                lines.append(f"{I2}    try:")
                lines.append(f"{I2}        {comp_var}.features.filletFeatures.add(_fi)")
                lines.append(f'{I2}        results.append(f"Step {idx}: {sname} r={round(r*10,1)}mm - {{_edges.count}} edges")')
                lines.append(f"{I2}    except Exception as _fe:")
                # RETRY: if descriptor edges caused compute error, try BB zone fallback
                if edge_descs and bboxes:
                    n_fb = len(bboxes)
                    lines.append(f"{I2}        try:")
                    lines.append(f"{I2}            _fb_body = get_body({comp_var}, '{body_name}') or find_body_for_edges({comp_var}, {bboxes}) or {comp_var}.bRepBodies.item(0)")
                    lines.append(f"{I2}            _fb_edges = find_edges_by_zone(_fb_body, {bboxes}, {n_fb})")
                    lines.append(f"{I2}            if _fb_edges.count > 0:")
                    lines.append(f"{I2}                _fi2={comp_var}.features.filletFeatures.createInput()")
                    lines.append(f"{I2}                _fi2.addConstantRadiusEdgeSet(_fb_edges, VI({r}), True)")
                    lines.append(f"{I2}                {comp_var}.features.filletFeatures.add(_fi2)")
                    lines.append(f'{I2}                results.append(f"Step {idx}: {sname} r={round(r*10,1)}mm - {{_fb_edges.count}} edges (BB retry)")')
                    lines.append(f"{I2}            else:")
                    lines.append(f'{I2}                results.append(f"Step {idx}: {sname} ERROR - {{_fe}}")')
                    lines.append(f"{I2}        except Exception as _fe2:")
                    lines.append(f"{I2}            if 'FILLET_NO_EDGE' in str(_fe2) and {r} < 0.05:")
                    lines.append(f'{I2}                results.append(f"Step {idx}: {sname} SKIPPED - cosmetic fillet too small")')
                    lines.append(f"{I2}            else:")
                    lines.append(f'{I2}                results.append(f"Step {idx}: {sname} ERROR - {{_fe2}} (BB retry also failed)")')
                else:
                    lines.append(f"{I2}        if 'FILLET_NO_EDGE' in str(_fe) and {r} < 0.05:")
                    lines.append(f'{I2}            results.append(f"Step {idx}: {sname} SKIPPED - cosmetic fillet too small")')
                    lines.append(f"{I2}        else:")
                    lines.append(f'{I2}            results.append(f"Step {idx}: {sname} ERROR - {{_fe}}")')
                lines.append(f"{I2}else:")
                lines.append(f'{I2}    results.append("Step {idx}: {sname} SKIPPED")')
            else:
                lines.append(f'{I2}results.append("Step {idx}: {sname} SKIPPED - no data")')

        elif stype == 'ChamferFeature':
            edges_data = step.get('edge_sets', [])
            edge_descs = step.get('edge_descriptors', [])
            faces = step.get('faces', [])
            if edges_data and (edge_descs or faces):
                d = edges_data[0].get('distance', 0.1)
                bboxes = [(f['bb_min'], f['bb_max']) for f in faces if 'bb_min' in f]
                if edge_descs:
                    lines.append(f"{I2}_edges = find_edges_by_descriptors({comp_var}, {edge_descs})")
                    if bboxes:
                        n_faces = len(bboxes)
                        lines.append(f"{I2}if _edges.count == 0:")
                        lines.append(f"{I2}    _body = get_body({comp_var}, '{body_name}') or find_body_for_edges({comp_var}, {bboxes}) or {comp_var}.bRepBodies.item(0)")
                        lines.append(f"{I2}    _edges = find_edges_by_zone(_body, {bboxes}, {n_faces})")
                elif bboxes:
                    n_faces = len(bboxes)
                    lines.append(f"{I2}_body = get_body({comp_var}, '{body_name}') or find_body_for_edges({comp_var}, {bboxes}) or {comp_var}.bRepBodies.item(0)")
                    lines.append(f"{I2}_edges = find_edges_by_zone(_body, {bboxes}, {n_faces})")
                else:
                    lines.append(f"{I2}_edges = adsk.core.ObjectCollection.create()")
                lines.append(f"{I2}if _edges.count > 0:")
                lines.append(f"{I2}    _chi={comp_var}.features.chamferFeatures.createInput2()")
                lines.append(f"{I2}    _chi.chamferEdgeSets.addEqualDistanceChamferEdgeSet(_edges, VI({d}), True)")
                lines.append(f"{I2}    try:")
                lines.append(f"{I2}        {comp_var}.features.chamferFeatures.add(_chi)")
                lines.append(f'{I2}        results.append(f"Step {idx}: {sname} - {{_edges.count}} edges")')
                lines.append(f"{I2}    except Exception as _ce:")
                # RETRY: if descriptor edges caused compute error, try BB zone fallback
                if edge_descs and bboxes:
                    n_fb = len(bboxes)
                    lines.append(f"{I2}        try:")
                    lines.append(f"{I2}            _fb_body = get_body({comp_var}, '{body_name}') or find_body_for_edges({comp_var}, {bboxes}) or {comp_var}.bRepBodies.item(0)")
                    lines.append(f"{I2}            _fb_edges = find_edges_by_zone(_fb_body, {bboxes}, {n_fb})")
                    lines.append(f"{I2}            if _fb_edges.count > 0:")
                    lines.append(f"{I2}                _chi2={comp_var}.features.chamferFeatures.createInput2()")
                    lines.append(f"{I2}                _chi2.chamferEdgeSets.addEqualDistanceChamferEdgeSet(_fb_edges, VI({d}), True)")
                    lines.append(f"{I2}                {comp_var}.features.chamferFeatures.add(_chi2)")
                    lines.append(f'{I2}                results.append(f"Step {idx}: {sname} - {{_fb_edges.count}} edges (BB retry)")')
                    lines.append(f"{I2}            else:")
                    lines.append(f'{I2}                results.append(f"Step {idx}: {sname} ERROR - {{_ce}}")')
                    lines.append(f"{I2}        except Exception as _ce2:")
                    lines.append(f'{I2}            results.append(f"Step {idx}: {sname} ERROR - {{_ce2}} (BB retry also failed)")')
                else:
                    lines.append(f'{I2}        results.append(f"Step {idx}: {sname} ERROR - {{_ce}}")')
                lines.append(f"{I2}else:")
                lines.append(f'{I2}    results.append("Step {idx}: {sname} SKIPPED")')
            else:
                lines.append(f'{I2}results.append("Step {idx}: {sname} SKIPPED")')

        elif stype == 'RevolveFeature':
            var = f"rev_{idx}"
            feature_vars[idx] = var
            op = step.get('operation', 1)
            angle = step.get('angle', 360.0)
            profile_indices = step.get('profile_indices', [])
            prof = step.get('profile', {})
            axis_dir = step.get('axis_direction')
            op_names = {0:'JoinFeatureOperation',1:'CutFeatureOperation',3:'NewBodyFeatureOperation'}
            op_name = op_names.get(op, 'CutFeatureOperation')
            prev_sk = None
            for pi in range(idx-1, -1, -1):
                ps = data['timeline'][pi]
                if ps.get('type') == 'Sketch':
                    prev_sk = sketch_vars.get(ps.get('name'))
                    break
            angle_rad = round(_math.radians(angle), 6)
            lines.append(f"{I2}{var} = None")

            # Determine axis deterministically from axis_direction
            if axis_dir:
                if abs(axis_dir[2]) > 0.9:
                    axis_expr = f"{comp_var}.zConstructionAxis"
                elif abs(axis_dir[1]) > 0.9:
                    axis_expr = f"{comp_var}.yConstructionAxis"
                else:
                    axis_expr = f"{comp_var}.xConstructionAxis"
            else:
                axis_expr = None  # will use try-all fallback

            if prev_sk:
                if prof:
                    a = prof.get('area', 0)
                    cx, cy = prof.get('centroid', [0,0])
                    lines.append(f"{I2}_p = find_profile({prev_sk}, {a}, {cx}, {cy}, tol_a=0.3, tol_p=0.3)")
                else:
                    lines.append(f"{I2}_p = {prev_sk}.profiles.item(0)")

                if axis_expr:
                    # Deterministic axis
                    lines.append(f"{I2}_ri = {comp_var}.features.revolveFeatures.createInput(_p, {axis_expr}, adsk.fusion.FeatureOperations.{op_name})")
                    lines.append(f"{I2}_ri.setAngleExtent(False, VI({angle_rad}))")
                    lines.append(f"{I2}{var} = {comp_var}.features.revolveFeatures.add(_ri)")
                    lines.append(f'{I2}results.append("Step {idx}: {sname}")')
                else:
                    # Fallback: try all axes
                    lines.append(f"{I2}for _ax in [{comp_var}.zConstructionAxis, {comp_var}.yConstructionAxis, {comp_var}.xConstructionAxis]:")
                    lines.append(f"{I2}    try:")
                    lines.append(f"{I2}        _ri = {comp_var}.features.revolveFeatures.createInput(_p, _ax, adsk.fusion.FeatureOperations.{op_name})")
                    lines.append(f"{I2}        _ri.setAngleExtent(False, VI({angle_rad}))")
                    lines.append(f"{I2}        {var} = {comp_var}.features.revolveFeatures.add(_ri)")
                    lines.append(f'{I2}        results.append("Step {idx}: {sname}"); break')
                    lines.append(f"{I2}    except: pass")
                    lines.append(f"{I2}else:")
                    lines.append(f'{I2}    results.append("Step {idx}: {sname} FAILED - all axes")')
            else:
                lines.append(f'{I2}results.append("Step {idx}: {sname} SKIPPED - no sketch")')

        elif stype == 'CircularPatternFeature':
            qty = step.get('quantity', 2)
            axis_dir = step.get('axis_direction')
            ta = round(_math.radians(step.get('total_angle', 360)), 6)

            # Collect features to pattern: prefer revolves, fall back to recent features
            all_feats = [(fi, feature_vars[fi]) for fi in sorted(feature_vars) if fi < idx and fi > idx - 5]
            rev_feats = [fv for fi, fv in all_feats if fv.startswith('rev_')]
            prev_feats = rev_feats if rev_feats else [fv for _, fv in all_feats[-3:]]

            if prev_feats:
                lines.append(f"{I2}_pc = adsk.core.ObjectCollection.create()")
                for fv in prev_feats:
                    lines.append(f"{I2}if {fv}: _pc.add({fv})")
                lines.append(f"{I2}if _pc.count > 0:")

                # Build axis list: deterministic from axis_direction, then fallbacks
                if axis_dir:
                    if abs(axis_dir[2]) > 0.9:
                        primary_axis = f"{comp_var}.zConstructionAxis"
                    elif abs(axis_dir[1]) > 0.9:
                        primary_axis = f"{comp_var}.yConstructionAxis"
                    else:
                        primary_axis = f"{comp_var}.xConstructionAxis"
                    lines.append(f"{I2}    _axes = [{primary_axis}]")
                else:
                    # No axis data: try cylinder face then construction axes
                    lines.append(f"{I2}    _cf = None; _cf_r = 0")
                    lines.append(f"{I2}    _body = get_body({comp_var}, '{body_name}') or {comp_var}.bRepBodies.item(0)")
                    lines.append(f"{I2}    for _fi in range(_body.faces.count):")
                    lines.append(f"{I2}        try:")
                    lines.append(f"{I2}            _fg = _body.faces.item(_fi).geometry")
                    lines.append(f"{I2}            if _fg.objectType.endswith('Cylinder') and abs(_fg.axis.z)>0.9 and _fg.radius > _cf_r:")
                    lines.append(f"{I2}                _cf = _body.faces.item(_fi); _cf_r = _fg.radius")
                    lines.append(f"{I2}        except: pass")
                    lines.append(f"{I2}    _axes = [_cf, {comp_var}.zConstructionAxis, {comp_var}.yConstructionAxis] if _cf else [{comp_var}.zConstructionAxis, {comp_var}.yConstructionAxis]")

                lines.append(f"{I2}    _done = False")
                lines.append(f"{I2}    for _ax in _axes:")
                lines.append(f"{I2}        try:")
                lines.append(f"{I2}            _pi = {comp_var}.features.circularPatternFeatures.createInput(_pc, _ax)")
                lines.append(f"{I2}            _pi.quantity = VI({qty})")
                lines.append(f"{I2}            _pi.totalAngle = VI({ta})")
                lines.append(f"{I2}            {comp_var}.features.circularPatternFeatures.add(_pi)")
                lines.append(f'{I2}            _done = True; break')
                lines.append(f"{I2}        except: pass")
                # Fallback: try each feature individually (multi-body cuts may fail as group)
                lines.append(f"{I2}    if not _done and _pc.count > 1:")
                lines.append(f"{I2}        for _fi in range(_pc.count):")
                lines.append(f"{I2}            _single = adsk.core.ObjectCollection.create()")
                lines.append(f"{I2}            _single.add(_pc.item(_fi))")
                lines.append(f"{I2}            for _ax in _axes:")
                lines.append(f"{I2}                try:")
                lines.append(f"{I2}                    _pi = {comp_var}.features.circularPatternFeatures.createInput(_single, _ax)")
                lines.append(f"{I2}                    _pi.quantity = VI({qty})")
                lines.append(f"{I2}                    _pi.totalAngle = VI({ta})")
                lines.append(f"{I2}                    {comp_var}.features.circularPatternFeatures.add(_pi)")
                lines.append(f'{I2}                    _done = True; break')
                lines.append(f"{I2}                except: pass")
                lines.append(f"{I2}    if _done:")
                lines.append(f'{I2}        results.append("Step {idx}: {sname} x{qty}")')
                lines.append(f"{I2}    else:")
                lines.append(f'{I2}        results.append("Step {idx}: {sname} FAILED - all axes")')
                lines.append(f"{I2}else:")
                lines.append(f'{I2}    results.append("Step {idx}: {sname} SKIPPED - no features")')
            else:
                lines.append(f'{I2}results.append("Step {idx}: {sname} SKIPPED")')

        elif stype == 'ConstructionPlane':
            var = f"plane_{idx}"
            plane_vars[idx] = var
            # Prefer geometry_origin.z (signed, exact) over offset (unsigned)
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
            lines.append(f'{I2}results.append("Step {idx}: {sname} z={round(abs_z*10,1)}mm")')

        elif stype == 'Occurrence':
            cname = step.get('component_name', 'component')
            # Stay in rootComp — creating sub-components fails in Part Design mode
            # Track body for participantBodies isolation
            comp_names[cname] = "rootComp"
            comp_var = "rootComp"
            lines.append(f'{I2}results.append("Step {idx}: {cname} (bodies in rootComp)")')

        else:
            lines.append(f'{I2}results.append("Step {idx}: {sname} ({stype}) SKIPPED")')

        lines.append(f"{I}except Exception as _e:")
        lines.append(f'{I}    results.append(f"Step {idx}: {sname} ERROR - {{_e}}")')
        lines.append("")

    lines.append(f"{I}print('\\n'.join(results))")
    lines.append(f"{I}ok = sum(1 for r in results if 'ERROR' not in r and 'FAILED' not in r and 'SKIPPED' not in r)")
    lines.append(f"{I}print(f'Done! {{ok}}/{{len(results)}} steps succeeded')")
    lines.append("except Exception as e:")
    lines.append("    print(f'FATAL: {e}')")
    lines.append("    import traceback; traceback.print_exc()")
    return '\n'.join(lines)
