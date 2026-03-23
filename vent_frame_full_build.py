"""
Ventilation Frame — Full Build Script for Fusion 360
=====================================================
Builds the complete assembly from scratch in one pass:
  1. External Frame (190×190×15mm) with widened slot and sphere snap bumps
  2. Insert Bottom (170×170×7mm) with pilot holes, snap recesses, fillets
  3. Insert Top (170×170×4mm + tabs) with clearance holes, counterbores, fillets

Run via Fusion MCP Bridge or paste into Fusion 360 Script Editor.
"""

import adsk.core, adsk.fusion, math

app = adsk.core.Application.get()
design = app.activeProduct
rootComp = design.rootComponent

V = adsk.core.ValueInput.createByReal
VS = adsk.core.ValueInput.createByString
P = adsk.core.Point3D.create
exts = rootComp.features.extrudeFeatures
xy = rootComp.xYConstructionPlane
yz = rootComp.yZConstructionPlane
xz = rootComp.xZConstructionPlane

# ═══════════════════════════════════════════════════════════════
# PARAMETERS — change these to adjust the design
# ═══════════════════════════════════════════════════════════════

# External Frame
FRAME_OUTER = 19.0          # 190mm outer dimension
FRAME_HEIGHT = 1.5          # 15mm tall
FRAME_INNER_LOWER = 15.6    # 156mm lower opening (vent channel)
FRAME_INNER_UPPER = 16.0    # 160mm upper slot (original, before widening)
FRAME_STEP_Z = 0.75         # step between lower and upper sections
FRAME_CORNER_POS = 8.5      # corner hole position from center
FRAME_CORNER_HOLE_R = 0.2   # corner through-hole radius (2mm)
FRAME_CORNER_CBORE_R = 0.375  # corner counterbore radius (3.75mm)
FRAME_FILLET_R = 0.3        # inner fillet radius (3mm)
FRAME_BOTTOM_STEP_OUTER = 18.7  # bottom step outer (±9.35)
FRAME_BOTTOM_STEP_INNER = 18.1  # bottom step inner (±9.05)
FRAME_BOTTOM_STEP_H = 0.15     # bottom step height (1.5mm)
FRAME_CHAMFER1 = 0.175      # chamfer on corner holes
FRAME_CHAMFER2 = 0.15       # chamfer on bottom step

# Widened slot (our modification)
SLOT_WIDTH = 17.06          # 170.6mm widened slot

# Snap bumps (spheres, 20% volume protrusion)
BUMP_SPHERE_R = 0.155       # sphere radius 1.55mm
BUMP_Z = 1.125              # Z center (matches original Plane55 offset)
BUMP_PROTRUSION = 0.575 * BUMP_SPHERE_R  # ~0.89mm

# Insert
INSERT_OUTER = 17.04        # 170.4mm (0.1mm clearance per side)
INSERT_INNER = 15.0         # 150mm mesh opening (= airflow)
INSERT_TOTAL_H = 1.1        # 11mm total height
INSERT_Z_BOT = 0.75         # sits on frame ledge
INSERT_Z_TOP = INSERT_Z_BOT + INSERT_TOTAL_H  # 1.85
INSERT_SPLIT_Z = INSERT_Z_BOT + 0.7  # 1.45 (bottom=7mm, top=4mm)

# Grip tabs
TAB_W = 3.0                 # 30mm wide
TAB_H = 0.5                 # 5mm protrusion above insert
TAB_EXPAND = 0.25           # 2.5mm taper expansion
TAB_OVERLAP = 0.1           # 1mm overlap into insert body

# Snap recesses on insert (blind holes matching bump spheres)
SNAP_RECESS_R = 0.15        # 1.5mm radius (matches bump base circle ~2.8mm dia)
SNAP_RECESS_DEPTH = 0.12    # 1.2mm deep (bump protrudes 0.89mm)

# Screws (16 total, 4 per side)
SCREW_HEAD_DIA = 0.566      # 5.66mm
SCREW_HEIGHT = 0.96         # 9.6mm total
SCREW_THREAD_DIA = 0.285    # 2.85mm
PILOT_R = 0.11              # 2.2mm pilot hole dia
CLEAR_R = 0.15              # 3.0mm clearance hole dia
CBORE_R = 0.30              # 6.0mm counterbore dia
CBORE_H = 0.20              # 2.0mm counterbore depth
FILLET_R = 0.05             # 0.5mm fillet on insert edges

# Screw positions: offsets from center along each side
SCREW_S1 = 5.7              # near corner (57mm from center)
SCREW_S2 = 2.0              # near grip tab (20mm from center)

# ═══════════════════════════════════════════════════════════════
# DERIVED VALUES
# ═══════════════════════════════════════════════════════════════

HF = FRAME_OUTER / 2        # 9.5 frame half
HW = INSERT_OUTER / 2       # 8.5 insert half outer
HI = INSERT_INNER / 2       # 7.5 insert half inner
FC = (HW + HI) / 2          # 8.0 screw wall center
WALL_POS = SLOT_WIDTH / 2   # 8.53 frame slot wall position
SC_OFF = BUMP_SPHERE_R - BUMP_PROTRUSION  # sphere center offset inside wall

# 16 screw positions (symmetric, 4 per side)
SCREWS = list(set(
    [(s, FC) for s in [SCREW_S1, SCREW_S2, -SCREW_S1, -SCREW_S2]] +
    [(s, -FC) for s in [SCREW_S1, SCREW_S2, -SCREW_S1, -SCREW_S2]] +
    [(FC, s) for s in [SCREW_S1, SCREW_S2, -SCREW_S1, -SCREW_S2]] +
    [(-FC, s) for s in [SCREW_S1, SCREW_S2, -SCREW_S1, -SCREW_S2]]
))

# ═══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def offP(ref, d):
    """Create offset construction plane."""
    inp = rootComp.constructionPlanes.createInput()
    inp.setByOffset(ref, V(d))
    return rootComp.constructionPlanes.add(inp)

def rect(sk, cx, cy, w, h):
    """Draw a rectangle in a sketch."""
    L = sk.sketchCurves.sketchLines
    x0, y0 = cx - w/2, cy - h/2
    x1, y1 = cx + w/2, cy + h/2
    L.addByTwoPoints(P(x0,y0,0), P(x1,y0,0))
    L.addByTwoPoints(P(x1,y0,0), P(x1,y1,0))
    L.addByTwoPoints(P(x1,y1,0), P(x0,y1,0))
    L.addByTwoPoints(P(x0,y1,0), P(x0,y0,0))

def ringProf(sk):
    """Find the ring profile (2 loops) in a sketch with two concentric rectangles."""
    for i in range(sk.profiles.count):
        if sk.profiles.item(i).profileLoops.count == 2:
            return sk.profiles.item(i)
    return sk.profiles.item(0)

def smallestProf(sk):
    """Find the smallest area profile in a sketch."""
    pf = None; ma = 999
    for j in range(sk.profiles.count):
        a = sk.profiles.item(j).areaProperties(
            adsk.fusion.CalculationAccuracy.MediumCalculationAccuracy).area
        if a < ma:
            ma = a; pf = sk.profiles.item(j)
    return pf

def circleProfs(sk, r):
    """Collect all profiles smaller than a circle of given radius."""
    c = adsk.core.ObjectCollection.create()
    lim = math.pi * r * r * 1.5
    for j in range(sk.profiles.count):
        a = sk.profiles.item(j).areaProperties(
            adsk.fusion.CalculationAccuracy.MediumCalculationAccuracy).area
        if a < lim:
            c.add(sk.profiles.item(j))
    return c

def findEdgesHzVert(body, min_length=0.5):
    """Find edges at horizontal-vertical face intersections."""
    edges = adsk.core.ObjectCollection.create()
    for i in range(body.edges.count):
        e = body.edges.item(i)
        if e.isDegenerate or e.length < min_length:
            continue
        try:
            f1, f2 = e.faces.item(0), e.faces.item(1)
            g1, g2 = f1.geometry, f2.geometry
            if hasattr(g1, 'normal') and hasattr(g2, 'normal'):
                n1, n2 = g1.normal, g2.normal
                if (abs(n1.z) > 0.9 and abs(n2.z) < 0.1) or \
                   (abs(n2.z) > 0.9 and abs(n1.z) < 0.1):
                    edges.add(e)
        except:
            pass
    return edges

# ═══════════════════════════════════════════════════════════════
# PART 1: EXTERNAL FRAME
# ═══════════════════════════════════════════════════════════════

print("=== EXTERNAL FRAME ===")

# [0-1] Outer block 190×190×15mm
sk0 = rootComp.sketches.add(xy)
rect(sk0, 0, 0, FRAME_OUTER, FRAME_OUTER)
e0 = exts.createInput(sk0.profiles.item(0),
    adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
e0.setDistanceExtent(False, V(FRAME_HEIGHT))
frameBody = exts.add(e0).bodies.item(0)
frameBody.name = "External Frame"
print("  Outer block")

# [2-3] Cut lower opening 156×156, full height
sk1 = rootComp.sketches.add(xy)
rect(sk1, 0, 0, FRAME_INNER_LOWER, FRAME_INNER_LOWER)
e1 = exts.createInput(sk1.profiles.item(0),
    adsk.fusion.FeatureOperations.CutFeatureOperation)
e1.setDistanceExtent(False, V(FRAME_HEIGHT))
e1.participantBodies = [frameBody]
exts.add(e1)
print("  Lower opening cut")

# [4-6] Step: cut ring from ±7.8 to ±8.0, from Z=1.5 downward 7.5mm
plane54 = offP(xy, FRAME_HEIGHT)  # Z=1.5
sk2 = rootComp.sketches.add(plane54)
rect(sk2, 0, 0, FRAME_INNER_UPPER, FRAME_INNER_UPPER)     # ±8.0
rect(sk2, 0, 0, FRAME_INNER_LOWER, FRAME_INNER_LOWER)     # ±7.8
e2 = exts.createInput(ringProf(sk2),
    adsk.fusion.FeatureOperations.CutFeatureOperation)
e2.setDistanceExtent(False, V(-FRAME_STEP_Z))  # -0.75 (downward from Z=1.5)
e2.participantBodies = [frameBody]
exts.add(e2)
print("  Step profile cut")

# [7-16] Original snap bumps at ±8.09 — SKIP (we'll add new sphere bumps later)
# The originals were at Z=1.125 (Plane55 offset) with R=0.15, revolve 360°
# We replace them with correctly positioned sphere bumps on the widened walls.

# [17] Fillet on inner edges (r=3mm)
try:
    fil_edges = adsk.core.ObjectCollection.create()
    for i in range(frameBody.edges.count):
        e = frameBody.edges.item(i)
        if e.isDegenerate:
            continue
        bb = e.boundingBox
        # Inner edges at the step (Z around 0.75, between ±7.8 and ±8.0)
        if e.length > 0.5 and abs(bb.minPoint.z - FRAME_STEP_Z) < 0.01:
            fil_edges.add(e)
    if fil_edges.count > 0:
        fi = rootComp.features.filletFeatures.createInput()
        fi.addConstantRadiusEdgeSet(fil_edges, V(FRAME_FILLET_R), True)
        rootComp.features.filletFeatures.add(fi)
        print(f"  Fillet: {fil_edges.count} edges, r={FRAME_FILLET_R*10:.0f}mm")
except Exception as ex:
    print(f"  Fillet skipped: {ex}")

# [18-19] Corner through-holes (4x R=2mm at ±8.5, ±8.5), cut full depth from top
sk3 = rootComp.sketches.add(plane54)
corners = [(FRAME_CORNER_POS, FRAME_CORNER_POS),
           (FRAME_CORNER_POS, -FRAME_CORNER_POS),
           (-FRAME_CORNER_POS, FRAME_CORNER_POS),
           (-FRAME_CORNER_POS, -FRAME_CORNER_POS)]
for cx, cy in corners:
    sk3.sketchCurves.sketchCircles.addByCenterRadius(P(cx, cy, 0), FRAME_CORNER_HOLE_R)
cp_holes = circleProfs(sk3, FRAME_CORNER_HOLE_R)
if cp_holes.count > 0:
    e3 = exts.createInput(cp_holes, adsk.fusion.FeatureOperations.CutFeatureOperation)
    e3.setDistanceExtent(False, V(-FRAME_HEIGHT))
    e3.participantBodies = [frameBody]
    exts.add(e3)
print("  Corner through-holes")

# [20-21] Corner counterbores (4x R=3.75mm, depth 10mm from top)
sk4 = rootComp.sketches.add(plane54)
for cx, cy in corners:
    sk4.sketchCurves.sketchCircles.addByCenterRadius(P(cx, cy, 0), FRAME_CORNER_CBORE_R)
cp_cbore = circleProfs(sk4, FRAME_CORNER_CBORE_R)
if cp_cbore.count > 0:
    e4 = exts.createInput(cp_cbore, adsk.fusion.FeatureOperations.CutFeatureOperation)
    e4.setDistanceExtent(False, V(-1.0))  # 10mm deep
    e4.participantBodies = [frameBody]
    exts.add(e4)
print("  Corner counterbores")

# [22] Chamfer on corner holes (d=1.75mm)
try:
    ch_edges = adsk.core.ObjectCollection.create()
    for i in range(frameBody.edges.count):
        e = frameBody.edges.item(i)
        if e.isDegenerate:
            continue
        geo = e.geometry
        if type(geo).__name__ == "Circle3D":
            bb = e.boundingBox
            # Corner hole edges at Z=1.5 (top face) near ±8.5
            if abs(bb.maxPoint.z - FRAME_HEIGHT) < 0.01:
                cx = (bb.maxPoint.x + bb.minPoint.x) / 2
                cy = (bb.maxPoint.y + bb.minPoint.y) / 2
                if abs(abs(cx) - FRAME_CORNER_POS) < 0.5 and abs(abs(cy) - FRAME_CORNER_POS) < 0.5:
                    r = (bb.maxPoint.x - bb.minPoint.x) / 2
                    if abs(r - FRAME_CORNER_CBORE_R) < 0.05:
                        ch_edges.add(e)
    if ch_edges.count > 0:
        chi = rootComp.features.chamferFeatures.createInput2()
        chi.setToEqualDistance(ch_edges, V(FRAME_CHAMFER1))
        rootComp.features.chamferFeatures.add(chi)
        print(f"  Chamfer1: {ch_edges.count} edges")
except Exception as ex:
    print(f"  Chamfer1 skipped: {ex}")

# [23-24] Bottom step ring cut (±9.35 to ±9.05, 1.5mm from bottom)
sk5 = rootComp.sketches.add(xy)
rect(sk5, 0, 0, FRAME_BOTTOM_STEP_OUTER, FRAME_BOTTOM_STEP_OUTER)
rect(sk5, 0, 0, FRAME_BOTTOM_STEP_INNER, FRAME_BOTTOM_STEP_INNER)
rp5 = ringProf(sk5)
if rp5:
    e5 = exts.createInput(rp5, adsk.fusion.FeatureOperations.CutFeatureOperation)
    e5.setDistanceExtent(False, V(FRAME_BOTTOM_STEP_H))
    e5.participantBodies = [frameBody]
    exts.add(e5)
print("  Bottom step")

# [25] Chamfer on bottom step (d=1.5mm)
try:
    ch2_edges = adsk.core.ObjectCollection.create()
    for i in range(frameBody.edges.count):
        e = frameBody.edges.item(i)
        if e.isDegenerate or e.length < 1.0:
            continue
        bb = e.boundingBox
        if abs(bb.maxPoint.z - FRAME_BOTTOM_STEP_H) < 0.02:
            ch2_edges.add(e)
    if ch2_edges.count > 0:
        chi2 = rootComp.features.chamferFeatures.createInput2()
        chi2.setToEqualDistance(ch2_edges, V(FRAME_CHAMFER2))
        rootComp.features.chamferFeatures.add(chi2)
        print(f"  Chamfer2: {ch2_edges.count} edges")
except Exception as ex:
    print(f"  Chamfer2 skipped: {ex}")

# === WIDEN SLOT (our modification: 160mm → 170.6mm) ===
sk_w = rootComp.sketches.add(offP(xy, FRAME_STEP_Z))
rect(sk_w, 0, 0, SLOT_WIDTH, SLOT_WIDTH)               # ±8.53
rect(sk_w, 0, 0, FRAME_INNER_UPPER, FRAME_INNER_UPPER)  # ±8.0
ew = exts.createInput(ringProf(sk_w), adsk.fusion.FeatureOperations.CutFeatureOperation)
ew.setDistanceExtent(False, V(FRAME_HEIGHT - FRAME_STEP_Z))
ew.participantBodies = [frameBody]
exts.add(ew)
print("  Slot widened to 170.6mm")

# === SPHERE SNAP BUMPS (4x on widened walls) ===
revolves = rootComp.features.revolveFeatures
bump_cfgs = [
    (xz,  WALL_POS + SC_OFF, P(0,  WALL_POS + SC_OFF, BUMP_Z)),
    (xz, -(WALL_POS + SC_OFF), P(0, -(WALL_POS + SC_OFF), BUMP_Z)),
    (yz,  WALL_POS + SC_OFF, P(WALL_POS + SC_OFF, 0, BUMP_Z)),
    (yz, -(WALL_POS + SC_OFF), P(-(WALL_POS + SC_OFF), 0, BUMP_Z)),
]
sph_bodies = []
for ref, off, wp in bump_cfgs:
    pl = offP(ref, off)
    sk = rootComp.sketches.add(pl)
    sc = sk.modelToSketchSpace(wp)
    ax = sk.sketchCurves.sketchLines.addByTwoPoints(
        P(sc.x, sc.y - BUMP_SPHERE_R, 0), P(sc.x, sc.y + BUMP_SPHERE_R, 0))
    ax.isConstruction = True
    sk.sketchCurves.sketchArcs.addByThreePoints(
        P(sc.x, sc.y - BUMP_SPHERE_R, 0),
        P(sc.x + BUMP_SPHERE_R, sc.y, 0),
        P(sc.x, sc.y + BUMP_SPHERE_R, 0))
    sk.sketchCurves.sketchLines.addByTwoPoints(
        P(sc.x, sc.y - BUMP_SPHERE_R, 0), P(sc.x, sc.y + BUMP_SPHERE_R, 0))
    ri = revolves.createInput(smallestProf(sk), ax,
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ri.setAngleExtent(False, VS("360 deg"))
    sph_bodies.append(revolves.add(ri).bodies.item(0))

tc = adsk.core.ObjectCollection.create()
for s in sph_bodies:
    tc.add(s)
ci = rootComp.features.combineFeatures.createInput(frameBody, tc)
ci.operation = adsk.fusion.FeatureOperations.JoinFeatureOperation
rootComp.features.combineFeatures.add(ci)
print("  Sphere snap bumps (4x)")

print(f"  Frame complete: {frameBody.faces.count} faces")


# ═══════════════════════════════════════════════════════════════
# PART 2: INSERT ASSEMBLY
# ═══════════════════════════════════════════════════════════════

print("\n=== INSERT ===")

# --- Insert frame body (170×170×11mm) ---
sk_i = rootComp.sketches.add(offP(xy, INSERT_Z_BOT))
rect(sk_i, 0, 0, INSERT_OUTER, INSERT_OUTER)
rect(sk_i, 0, 0, INSERT_INNER, INSERT_INNER)
ei = exts.createInput(ringProf(sk_i),
    adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
ei.setDistanceExtent(False, V(INSERT_TOTAL_H))
insertBody = exts.add(ei).bodies.item(0)
insertBody.name = "Insert Base"
print("  Insert frame")

# --- Tapered grip tabs (4x, wedge shape) ---
zb = INSERT_Z_TOP - TAB_OVERLAP
zt = INSERT_Z_TOP + TAB_H
tab_defs = [
    (yz, [P(0, HI, zb), P(0, HW, zb), P(0, HW + TAB_EXPAND, zt), P(0, HI, zt)]),
    (yz, [P(0,-HI, zb), P(0,-HW, zb), P(0,-HW - TAB_EXPAND, zt), P(0,-HI, zt)]),
    (xz, [P(HI, 0, zb), P(HW, 0, zb), P(HW + TAB_EXPAND, 0, zt), P(HI, 0, zt)]),
    (xz, [P(-HI, 0, zb), P(-HW, 0, zb), P(-HW - TAB_EXPAND, 0, zt), P(-HI, 0, zt)]),
]
tab_bodies = []
for pl, wps in tab_defs:
    sk = rootComp.sketches.add(pl)
    sps = [sk.modelToSketchSpace(w) for w in wps]
    L = sk.sketchCurves.sketchLines
    for i in range(4):
        L.addByTwoPoints(P(sps[i].x, sps[i].y, 0),
                         P(sps[(i+1)%4].x, sps[(i+1)%4].y, 0))
    ti = exts.createInput(smallestProf(sk),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ti.setDistanceExtent(True, V(TAB_W / 2))
    tab_bodies.append(exts.add(ti).bodies.item(0))

tc2 = adsk.core.ObjectCollection.create()
for t in tab_bodies:
    tc2.add(t)
ci2 = rootComp.features.combineFeatures.createInput(insertBody, tc2)
ci2.operation = adsk.fusion.FeatureOperations.JoinFeatureOperation
rootComp.features.combineFeatures.add(ci2)

# Re-fetch insert body
insertBody = None
for i in range(rootComp.bRepBodies.count):
    b = rootComp.bRepBodies.item(i)
    if b.name == "Insert Base" and b.isVisible:
        insertBody = b
        break
print("  Tabs combined")

# --- Snap recesses (blind holes via Combine Cut) ---
snap_cfgs = [
    (xz, HW - SNAP_RECESS_DEPTH, P(0, HW - SNAP_RECESS_DEPTH, BUMP_Z)),
    (xz, -HW,                     P(0, -HW, BUMP_Z)),
    (yz, HW - SNAP_RECESS_DEPTH, P(HW - SNAP_RECESS_DEPTH, 0, BUMP_Z)),
    (yz, -HW,                     P(-HW, 0, BUMP_Z)),
]
snap_cyls = []
for ref, off, wp in snap_cfgs:
    pl = offP(ref, off)
    sk = rootComp.sketches.add(pl)
    sc = sk.modelToSketchSpace(wp)
    sk.sketchCurves.sketchCircles.addByCenterRadius(P(sc.x, sc.y, 0), SNAP_RECESS_R)
    ei_s = exts.createInput(sk.profiles.item(0),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ei_s.setDistanceExtent(False, V(SNAP_RECESS_DEPTH))
    snap_cyls.append(exts.add(ei_s).bodies.item(0))

tc3 = adsk.core.ObjectCollection.create()
for c in snap_cyls:
    tc3.add(c)
ci3 = rootComp.features.combineFeatures.createInput(insertBody, tc3)
ci3.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
rootComp.features.combineFeatures.add(ci3)
print("  Snap recesses (blind, 1.2mm deep)")

# --- Split into Top + Bottom ---
rootComp.features.splitBodyFeatures.add(
    rootComp.features.splitBodyFeatures.createInput(
        insertBody, offP(xy, INSERT_SPLIT_Z), True))

topBody = None
botBody = None
for i in range(rootComp.bRepBodies.count):
    b = rootComp.bRepBodies.item(i)
    bb = b.boundingBox
    if not b.isVisible or b.name == "External Frame":
        continue
    if bb.maxPoint.z > 2.0:
        topBody = b
    elif bb.maxPoint.z < 1.5 and bb.minPoint.z > 0.7:
        botBody = b

topBody.name = "Insert Top"
botBody.name = "Insert Bottom"
print(f"  Split: bottom={INSERT_SPLIT_Z - INSERT_Z_BOT:.0%}cm, top={INSERT_Z_TOP - INSERT_SPLIT_Z:.0%}cm")

# --- Pilot holes in bottom (Combine Cut approach) ---
sk_p = rootComp.sketches.add(offP(xy, INSERT_Z_BOT))
for x, y in SCREWS:
    sk_p.sketchCurves.sketchCircles.addByCenterRadius(P(x, y, 0), PILOT_R)
pp = circleProfs(sk_p, PILOT_R)
ei_p = exts.createInput(pp, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
ei_p.setDistanceExtent(False, V(INSERT_SPLIT_Z - INSERT_Z_BOT))
er_p = exts.add(ei_p)
cyls = adsk.core.ObjectCollection.create()
for i in range(er_p.bodies.count):
    cyls.add(er_p.bodies.item(i))
cp_p = rootComp.features.combineFeatures.createInput(botBody, cyls)
cp_p.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
rootComp.features.combineFeatures.add(cp_p)
print(f"  Pilot holes: {cyls.count}")

# --- Clearance holes in top ---
sk_c = rootComp.sketches.add(offP(xy, INSERT_SPLIT_Z))
for x, y in SCREWS:
    sk_c.sketchCurves.sketchCircles.addByCenterRadius(P(x, y, 0), CLEAR_R)
cp_c = circleProfs(sk_c, CLEAR_R)
cli = exts.createInput(cp_c, adsk.fusion.FeatureOperations.CutFeatureOperation)
cli.setAllExtent(adsk.fusion.ExtentDirections.PositiveExtentDirection)
cli.participantBodies = [topBody]
exts.add(cli)
print(f"  Clearance holes: {cp_c.count}")

# --- Counterbores in top ---
sk_cb = rootComp.sketches.add(offP(xy, INSERT_Z_TOP - CBORE_H))
for x, y in SCREWS:
    sk_cb.sketchCurves.sketchCircles.addByCenterRadius(P(x, y, 0), CBORE_R)
cb = circleProfs(sk_cb, CBORE_R)
cbi = exts.createInput(cb, adsk.fusion.FeatureOperations.CutFeatureOperation)
cbi.setDistanceExtent(False, V(CBORE_H))
cbi.participantBodies = [topBody]
exts.add(cbi)
print(f"  Counterbores: {cb.count}")

# --- Fillets on insert outer edges ---
fillets = rootComp.features.filletFeatures
for body, name in [(botBody, "Bottom"), (topBody, "Top")]:
    edges = findEdgesHzVert(body)
    if edges.count > 0:
        try:
            fi = fillets.createInput()
            fi.addConstantRadiusEdgeSet(edges, V(FILLET_R), True)
            fillets.add(fi)
            print(f"  Fillet {name}: {edges.count} edges, r=0.5mm")
        except:
            print(f"  Fillet {name}: skipped")

# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════

print("\n=== BUILD COMPLETE ===")
for i in range(rootComp.bRepBodies.count):
    b = rootComp.bRepBodies.item(i)
    if b.isVisible:
        bb = b.boundingBox
        w = (bb.maxPoint.x - bb.minPoint.x) * 10
        h = (bb.maxPoint.y - bb.minPoint.y) * 10
        d = (bb.maxPoint.z - bb.minPoint.z) * 10
        print(f"  {b.name}: {b.faces.count} faces, {w:.0f}x{h:.0f}x{d:.0f}mm")
