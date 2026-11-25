"""Pure-logic checks without Blender. Run: python tests/smoke_test.py"""
import math, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
bpy = types.ModuleType("bpy")
bpy.types = types.SimpleNamespace(PropertyGroup=object, Operator=object, Panel=object, WorkSpaceTool=object)
_p = lambda *a, **k: None
bpy.props = types.SimpleNamespace(**{n: _p for n in ["StringProperty", "EnumProperty", "BoolProperty", "IntProperty",
    "FloatProperty", "FloatVectorProperty", "PointerProperty", "CollectionProperty"]})
sys.modules["bpy"] = bpy
sys.modules["bpy.props"] = bpy.props
import shape_draw as sd

assert sd.snap_value(0.37, 0.25) == 0.25
assert sd.snap_value(0.38, 0.25) == 0.5
x, y = sd.snap_angle((0, 0), (1, 0.1))
assert abs(y) < 1e-9 and abs(x - (1 + 0.01) ** 0.5) < 1e-9
c = lambda xy: {"co": xy, "h_in": xy, "h_out": xy, "smooth": False}
sq = [c((0, 0)), c((1, 0)), c((1, 1)), c((0, 1))]
s = sd.sample_outline(sq, 12)
assert len(s) == 4 and all(p[1] for p in s)
assert sd.signed_area([p[0] for p in s]) == 1.0
smooth = dict(c((1, 0)), h_in=(1, -0.5), h_out=(1, 0.5), smooth=True)
s2 = sd.sample_outline([c((0, 0)), smooth, c((0, 1))], 8)
assert len(s2) == 3 + 7 + 7, len(s2)
assert sd.bounds_center([(0, 0), (2, 4)]) == (1, 2)
assert len(sd.dedupe_ring([((0, 0), 1, 0), ((0, 0), 1, 0), ((1, 0), 1, 0), ((0, 0), 1, 0)])) == 2
assert abs(sd.segment_length(c((0, 0)), c((3, 4))) - 5) < 1e-9
assert sd.segment_midpoint(c((0, 0)), c((2, 4))) == (1, 2)
k = 0.5523  # cubic quarter circle, radius 1
qa = dict(c((1, 0)), h_out=(1, k), smooth=True)
qb = dict(c((0, 1)), h_in=(k, 1), smooth=True)
assert abs(sd.segment_length(qa, qb) - math.pi / 2) < 1e-3, sd.segment_length(qa, qb)
mx, my = sd.segment_midpoint(qa, qb)
assert abs(math.hypot(mx, my) - 1) < 1e-3
px, py = sd.point_at_length((1, 1), (1, 9), 2.5)
assert abs(px - 1) < 1e-9 and abs(py - 3.5) < 1e-9
assert sd.point_at_length((0, 0), (0, 0), 2) == (2, 0)
qx, qy = sd.point_at_length((0, 0), (3, 4), 10)
assert abs(qx - 6) < 1e-9 and abs(qy - 8) < 1e-9
assert sd.lock_to_axis((1, 1), (5, 1.3)) == ((5, 1), 'X')
assert sd.lock_to_axis((1, 1), (0.8, -3)) == ((1, -3), 'Y')
assert sd.lock_to_axis((0, 0), (2, 1)) == ((2, 1), None)
assert sd.lock_to_axis((0, 0), (0, 0)) == ((0, 0), None)
r = sd.rect_points((1, 1), (3, 2))
assert [q["co"] for q in r] == [(1, 1), (3, 1), (3, 2), (1, 2)] and not any(q["smooth"] for q in r)
sq = sd.rect_points((0, 0), (-2, 0.5), square=True)
assert [q["co"] for q in sq] == [(0, 0), (-2, 0), (-2, 2), (0, 2)]
ci = sd.circle_points((1, 1), 2)
assert all(q["smooth"] for q in ci) and abs(sd.signed_area([q["co"] for q in ci]) - 8) < 1e-9
quarter = sd.segment_length(ci[0], ci[1])
assert abs(quarter - math.pi) < 2e-3, quarter  # a quarter of a radius-2 circle
print("SMOKE_OK")
