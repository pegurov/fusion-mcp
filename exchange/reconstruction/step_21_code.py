import adsk.core, adsk.fusion, math, traceback

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

# ── Step 21:  lid:1 ──
# Look up existing objects from previous steps
sk_0 = None
for _i in range(rootComp.sketches.count):
    if rootComp.sketches.item(_i).name == "deviceShape":
        sk_0 = rootComp.sketches.item(_i); break
sk_7 = None
for _i in range(rootComp.sketches.count):
    if rootComp.sketches.item(_i).name == "cylinderTop":
        sk_7 = rootComp.sketches.item(_i); break
sk_10 = None
for _i in range(rootComp.sketches.count):
    if rootComp.sketches.item(_i).name == "cylinderBottom":
        sk_10 = rootComp.sketches.item(_i); break
sk_13 = None
for _i in range(rootComp.sketches.count):
    if rootComp.sketches.item(_i).name == "capTeeth":
        sk_13 = rootComp.sketches.item(_i); break
sk_17 = None
for _i in range(rootComp.sketches.count):
    if rootComp.sketches.item(_i).name == "handle":
        sk_17 = rootComp.sketches.item(_i); break
plane_6 = None
for _i in range(rootComp.constructionPlanes.count):
    if rootComp.constructionPlanes.item(_i).name == "Plane1":
        plane_6 = rootComp.constructionPlanes.item(_i); break
ext_1 = None
for _ti in range(design.timeline.count):
    _ent = design.timeline.item(_ti).entity
    if hasattr(_ent, "name") and _ent.name == "Extrude1":
        ext_1 = _ent; break
ext_2 = None
for _ti in range(design.timeline.count):
    _ent = design.timeline.item(_ti).entity
    if hasattr(_ent, "name") and _ent.name == "Extrude4":
        ext_2 = _ent; break
ext_3 = None
for _ti in range(design.timeline.count):
    _ent = design.timeline.item(_ti).entity
    if hasattr(_ent, "name") and _ent.name == "Extrude6":
        ext_3 = _ent; break
ext_8 = None
for _ti in range(design.timeline.count):
    _ent = design.timeline.item(_ti).entity
    if hasattr(_ent, "name") and _ent.name == "Extrude13":
        ext_8 = _ent; break
ext_9 = None
for _ti in range(design.timeline.count):
    _ent = design.timeline.item(_ti).entity
    if hasattr(_ent, "name") and _ent.name == "Extrude14":
        ext_9 = _ent; break
ext_11 = None
for _ti in range(design.timeline.count):
    _ent = design.timeline.item(_ti).entity
    if hasattr(_ent, "name") and _ent.name == "Extrude15":
        ext_11 = _ent; break
ext_12 = None
for _ti in range(design.timeline.count):
    _ent = design.timeline.item(_ti).entity
    if hasattr(_ent, "name") and _ent.name == "Extrude16":
        ext_12 = _ent; break
rev_14 = None
for _ti in range(design.timeline.count):
    _ent = design.timeline.item(_ti).entity
    if hasattr(_ent, "name") and _ent.name == "Revolve1":
        rev_14 = _ent; break
rev_15 = None
for _ti in range(design.timeline.count):
    _ent = design.timeline.item(_ti).entity
    if hasattr(_ent, "name") and _ent.name == "Revolve2":
        rev_15 = _ent; break
ext_18 = None
for _ti in range(design.timeline.count):
    _ent = design.timeline.item(_ti).entity
    if hasattr(_ent, "name") and _ent.name == "Extrude17":
        ext_18 = _ent; break

try:
    _occ = rootComp.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    comp_lid = _occ.component
    comp_lid.name = "lid"
    print("Step 21: lid (sub-component)")
except Exception as _e:
    print(f"Step 21:  lid:1 ERROR - {_e}")
    import traceback; traceback.print_exc()