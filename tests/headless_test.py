"""Headless Blender test: registers the add-on and builds shapes via execute().
Run: blender --background --factory-startup --python tests/headless_test.py"""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy
import bmesh
from mathutils import Vector
import shape_draw as sd
sd.register()
print("REGISTER_OK")

def run(points, height, output='MESH', bevel=0.0, normal=(0, 0, 1), free_only=False, attach='OFF', target='', origin=(0, 0, 0)):
    op_cls = sd.VIEW3D_OT_shape_draw
    win = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
    # Call execute() through a fake self: background mode has no 3D view to invoke in.
    class Fake:
        pass
    f = Fake()
    f.points = [type("P", (), p) for p in points]
    for p in f.points:
        p.co, p.h_in, p.h_out = p.co, p.h_in, p.h_out
    f.plane_origin, f.plane_normal, f.plane_xaxis = origin, normal, (1, 0, 0)
    f.height, f.output, f.bevel, f.bevel_segments, f.resolution = height, output, bevel, 2, 12
    f.bevel_free_only = free_only
    f.attach, f.target = attach, target
    f.report = lambda lvl, msg: print("REPORT", lvl, msg)
    sd._point_dict = lambda p: {"co": p.co, "h_in": p.h_in, "h_out": p.h_out, "smooth": p.smooth}
    res = op_cls.execute(f, bpy.context)
    return res, bpy.context.view_layer.objects.active

c = lambda xy: {"co": xy, "h_in": xy, "h_out": xy, "smooth": False}
square = [c((0, 0)), c((2, 0)), c((2, 1)), c((0, 1))]
res, ob = run(square, 1.5)
me = ob.data
print("SQUARE", res, len(me.vertices), len(me.polygons), tuple(round(v, 3) for v in ob.dimensions), tuple(ob.location))
assert res == {'FINISHED'} and len(me.vertices) == 8 and len(me.polygons) == 6
assert abs(ob.dimensions.z - 1.5) < 1e-5

arch = [c((0, 0)), c((2, 0)), dict(c((1, 1)), h_in=(2, 1), h_out=(0, 1), smooth=True)]
res, ob = run(arch[::-1], 0.5, bevel=0.05)  # clockwise input must still face outwards
me = ob.data
print("ARCH", res, len(me.vertices), len(me.polygons), [m.type for m in ob.modifiers])
bpy.context.view_layer.update()
bm = bmesh.new(); bm.from_mesh(me)
vol = bm.calc_volume(signed=True)
print("ARCH_VOLUME", round(vol, 4))
assert vol > 0

res, ob = run(arch, -0.75, output='CURVE', bevel=0.1)
bpy.context.view_layer.update()
print("CURVE", res, ob.type, tuple(round(v, 3) for v in ob.dimensions), tuple(round(v, 3) for v in ob.location))
assert ob.type == 'CURVE' and abs(ob.dimensions.z - 0.75) < 1e-3

res, ob = run(square, 1.0, normal=(0, -1, 0))
print("WALL", res, tuple(round(v, 3) for v in ob.dimensions), tuple(round(v, 3) for v in ob.location))
res, ob = run(square, 1.0, bevel=0.1, free_only=True)
w = ob.data.attributes["bevel_weight_edge"].data
zs = {}
for e in ob.data.edges:
    z = tuple(round(ob.data.vertices[v].co.z, 3) for v in e.vertices)
    zs.setdefault(z, set()).add(round(w[e.index].value, 3))
print("FREE_ONLY", ob.modifiers[0].limit_method, zs)
assert ob.modifiers[0].limit_method == 'WEIGHT'
assert zs[(0.0, 0.0)] == {0.0} and zs[(1.0, 1.0)] == {1.0}
bpy.context.view_layer.update()
ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh()
low = [v.co.z for v in ev.vertices if 1e-4 < v.co.z < 0.5]
print("FREE_ONLY_LOW_VERTS", len(low))
assert not low  # walls meet the base square: no chamfer at the bottom
res, ob = run(square, 1.0, bevel=0.1, free_only=False)
bpy.context.view_layer.update()
ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh()
low = [v.co.z for v in ev.vertices if 1e-4 < v.co.z < 0.5]
print("ALL_EDGES_LOW_VERTS", len(low))
assert low  # the default still bevels the base

def volume(ob):
    bpy.context.view_layer.update()
    me = ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh()
    bm = bmesh.new(); bm.from_mesh(me)
    v = bm.calc_volume(signed=True)
    manifold = all(e.is_manifold for e in bm.edges)
    bm.free()
    return round(v, 4), manifold

def fresh_cube():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.mesh.primitive_cube_add(size=2)  # top face at z=1
    return bpy.context.active_object

inner = [c((-0.5, -0.5)), c((0.5, -0.5)), c((0.5, 0.5)), c((-0.5, 0.5))]

cube = fresh_cube()
res, ob = run(inner, 0.5, attach='PARENT', target=cube.name, origin=(0, 0, 1))
print("PARENT", res, ob.name, ob.parent and ob.parent.name, tuple(round(v, 3) for v in ob.matrix_world.translation))
assert ob.parent == cube and abs(ob.matrix_world.translation.z - 1.0) < 1e-6

cube = fresh_cube()
res, ob = run(inner, 0.5, attach='CUT', target=cube.name, origin=(0, 0, 1))
cutter = bpy.data.objects["Cut"]
print("CUT", res, ob.name, [m.type for m in cube.modifiers], cutter.hide_get(), cutter.parent.name, volume(cube))
assert ob == cube and cube.modifiers[-1].object == cutter and cutter.hide_get()
assert volume(cube) == (7.5, True)

cube = fresh_cube()
res, ob = run(inner, 0.25, attach='CUT', target=cube.name, origin=(0, 0, 1))  # positive drag still cuts in
print("CUT_POS", volume(cube))
assert volume(cube)[0] == 7.75

cube = fresh_cube()
res, ob = run(inner, 0.5, attach='JOIN', target=cube.name, origin=(0, 0, 1), output='CURVE')
print("JOIN", res, ob.name, [o.name for o in bpy.data.objects], len(cube.modifiers), volume(cube))
assert ob == cube and len(bpy.data.objects) == 1 and volume(cube) == (8.5, True)

res, ob = run(inner, 0.5, attach='JOIN', target='', origin=(0, 0, 1))  # drawn on the ground: attach ignored
print("NO_TARGET", res, ob.name, ob.parent)
assert ob.name.startswith("Shape") and ob.parent is None


# Parent onto a moved/rotated object whose matrix is stale (as right after undo).
cube = fresh_cube()
bpy.context.view_layer.update()
cube.location = (3, 1, 0.5); cube.rotation_euler = (0.4, 0, 0.9); cube.scale = (2, 1, 1)
res, ob = run(inner, 0.5, attach='PARENT', target=cube.name, origin=(0, 0, 5))
bpy.context.view_layer.update()
print("PARENT_MOVED", tuple(round(v, 4) for v in ob.matrix_world.translation))
assert (ob.matrix_world.translation - Vector((0, 0, 5))).length < 1e-5

# Edge marks: a plain box has no sharp edges or bevel weights, but still shades flat.
res, ob = run(square, 1.0)
me = ob.data
sharp = me.attributes.get("sharp_edge")
n_sharp = sum(1 for d in sharp.data if d.value) if sharp else 0
flat = all(not p.use_smooth for p in me.polygons)
print("MARKS_BOX", n_sharp, "bevel_weight_edge" in me.attributes, flat)
assert n_sharp == 0 and "bevel_weight_edge" not in me.attributes and flat
cn = me.corner_normals
assert all((cn[li].vector - p.normal).length < 1e-5 for p in me.polygons for li in p.loop_indices)
res, ob = run(arch, 0.5)
me = ob.data
print("MARKS_ARCH smooth faces:", sum(p.use_smooth for p in me.polygons), "of", len(me.polygons))
assert 0 < sum(p.use_smooth for p in me.polygons) < len(me.polygons)


# stored_world_matrix matches Blender's own result for a nested, transformed chain.
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
bpy.ops.mesh.primitive_cube_add(location=(2, -1, 3), rotation=(0.3, 0.2, 1.1), scale=(1.5, 1, 0.5))
a = bpy.context.active_object
bpy.ops.mesh.primitive_cube_add(location=(-4, 2, 1), rotation=(1.0, 0, 0.4), scale=(1, 2, 1))
b = bpy.context.active_object
bpy.context.view_layer.update()
sd.parent_keep_world(bpy.context, b, a, b.matrix_world.copy())
b.location.x += 0.7; b.rotation_euler.z += 0.5
bpy.ops.mesh.primitive_cube_add(location=(1, 1, 1), rotation=(0, 0.6, 0))
c = bpy.context.active_object
bpy.context.view_layer.update()
sd.parent_keep_world(bpy.context, c, b, c.matrix_world.copy())
c.scale = (1.2, 0.8, 1)
bpy.context.view_layer.update()
err = max(abs(x - y) for r1, r2 in zip(sd.stored_world_matrix(c), c.matrix_world) for x, y in zip(r1, r2))
print("STORED_WORLD_ERR", round(err, 8))
assert err < 1e-5

# Cut onto a moved/rotated object: the cutter must overlap it.
cube = fresh_cube()
cube.location = (4, -2, 1); cube.rotation_euler = (0, 0, 0.8)
bpy.context.view_layer.update()
top = cube.matrix_world @ Vector((0, 0, 1))
res, ob = run(inner, 0.5, attach='CUT', target=cube.name, origin=tuple(top))
cutter = bpy.data.objects["Cut"]
bpy.context.view_layer.update()
print("CUT_MOVED cutter at", tuple(round(v, 3) for v in cutter.matrix_world.translation), "volume", volume(cube))
assert (cutter.matrix_world.translation - top).length < 0.01 and volume(cube)[0] == 7.5

sd.unregister()
# Curve Segments only shows for outlines that have a curve
P = type("P", (), {})
def as_props(points):
    out = []
    for d in points:
        p = P(); p.co, p.h_in, p.h_out, p.smooth = d["co"], d["h_in"], d["h_out"], d["smooth"]
        out.append(p)
    return out
has = lambda pts: sd.VIEW3D_OT_shape_draw._has_curves(type("F", (), {"points": as_props(pts)})())
print("HAS_CURVES", has(square), has(arch))
assert not has(square) and has(arch)

# Quick shapes build like any outline
res, ob = run(sd.rect_points((0, 0), (2, 1)), 1.0)
print("QUICK_RECT", res, round(volume(ob)[0], 4))
assert volume(ob) == (2.0, True)
res, ob = run(sd.circle_points((0, 0), 1.0), 1.0)
v = volume(ob)
print("QUICK_CIRCLE", res, v)
assert v[1] and abs(v[0] - math.pi) < 0.02  # 12 segments per quarter: close to pi

# Re-edit: the outline is stored on the object and rebuilds it in place
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
res, ob = run(sd.rect_points((0, 0), (2, 1)), 1.0, bevel=0.05, origin=(0, 0, 0))
d = sd.load_outline(ob)
print("STORED", [q["co"] for q in d["points"]], d["height"], d["output"])
assert [q["co"] for q in d["points"]] == [(-1, -0.5), (1, -0.5), (1, 0.5), (-1, 0.5)] and d["height"] == 1.0
ob.location = (5, 0, 0)  # moved by the user afterwards
bpy.context.view_layer.update()
before_matrix = ob.matrix_world.copy()
mats = len(ob.data.materials)
d["points"][1] = sd.corner_point({"co": (2, -0.5)})   # drag a corner out by 1 (handles move with it)
d["points"][2] = sd.corner_point({"co": (2, 0.5)})
old = sd.rebuild_from_outline(ob, d)
bm = bmesh.new(); bm.from_mesh(ob.data); raw = round(bm.calc_volume(signed=True), 4); bm.free()
print("REBUILT", raw, volume(ob), [(m.type, round(m.width, 3), m.segments, m.limit_method) for m in ob.modifiers], old is not ob.data)
assert raw == 3.0 and volume(ob)[1] and [m.type for m in ob.modifiers] == ['BEVEL']
assert ob.matrix_world == before_matrix and len(ob.data.materials) == mats

# the edit operator's own steps, on a fake self
E = sd.VIEW3D_OT_shape_draw_edit
F = type("FE", (), {k: v for k, v in E.__dict__.items() if callable(v)})
f = F()
f.ob, f.d = ob, sd.load_outline(ob)
f.pts = [dict(q) for q in f.d["points"]]
f.original_data = ob.data
f.report = lambda lvl, msg: print("REPORT", lvl, msg)
i = f._insert(0, 0.5)                    # click the bottom edge: new corner in the middle
assert len(f.pts) == 5 and f.pts[i]["co"] == (0.0, -0.5)
f._toggle_smooth(i)                      # turn it into a curve point
assert f.pts[i]["smooth"]
f.pts[i] = dict(f.pts[i], co=(0.0, -1.0), h_in=(-0.6, -1.0), h_out=(0.6, -1.0))  # pull it down
f._set_height(2.0)
f._rebuild()
v = volume(ob)
print("EDITED", len(f.pts), v)
assert v[1] and v[0] > 2 * 2.0  # taller, and the bulge adds area
assert f._delete(i) and len(f.pts) == 4
f.pts = f.pts[:2]
assert not f._delete(0)                  # can't go below a closable outline

# a cut's outline is reachable from the object it cuts, and stays a cut
cube = fresh_cube()
res, ob = run(inner, 0.5, attach='CUT', target=cube.name, origin=(0, 0, 1))
owner = sd.outline_owner(cube)
dc = sd.load_outline(owner)
print("CUT_OWNER", owner.name, dc["cut"], round(dc["height"], 4))
assert owner.name.startswith("Cut") and dc["cut"] and dc["height"] < 0

# a curve shape stores its base below the object's middle
res, ob = run(arch, 0.8, output='CURVE')
dcv = sd.load_outline(ob)
print("CURVE_STORED", dcv["output"], dcv["base_z"])
assert dcv["output"] == 'CURVE' and abs(dcv["base_z"] + 0.4) < 1e-9

print("ALL_OK")
