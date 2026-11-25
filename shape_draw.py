# SPDX-License-Identifier: GPL-3.0-or-later
"""Shape Draw: click out a 2D outline (with optional bezier curves) on the
ground or on a surface, then pull it up into a solid mesh in one go.

Use it from the toolbar ("Draw Shape" tool) or Add menu > Draw Shape.

Drawing:
  LMB click          add a sharp corner
  LMB drag           add a smooth point, dragging pulls out its curve handles
  click first point  close the outline (or Enter / Space)
  type a number      exact length for the next edge, aimed with the mouse
                     (Enter / click places it, Esc stops typing)
  Shift              lock the new edge to 15 degree steps
  Ctrl               turn grid snapping off while held (edges still lock onto
                     the X / Y axis when nearly lined up; "Lock to Axis" turns that off)
  Backspace / Ctrl+Z remove the last point
  P / R / C          switch to outline / rectangle / circle (also in the tool header)
  Esc / RMB          cancel
Rectangle:
  click or drag      corner to corner (Shift: square)
Circle:
  click, then click or drag   centre, then radius (type a number for an exact radius)
Extruding:
  move mouse         set the height (snaps to the grid step)
  type a number      exact height (Backspace edits, - flips)
  LMB / Enter        finish
  RMB                back to drawing
"""

bl_info = {
    "name": "Shape Draw",
    "author": "DevTomUK",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Toolbar > Draw Shape, or Add > Draw Shape",
    "description": "Click out an outline with curved edges, then pull it into a mesh",
    "category": "Add Mesh",
}

import math

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       FloatProperty, FloatVectorProperty, IntProperty,
                       PointerProperty, StringProperty)

try:  # only available inside Blender; the smoke test stubs bpy alone
    import blf
    import bmesh
    import gpu
    from bpy_extras import view3d_utils
    from gpu_extras.batch import batch_for_shader
    from mathutils import Matrix, Vector, geometry
except ImportError:  # pragma: no cover
    pass


# ---------------------------------------------------------------------------
# Pure 2D maths (no bpy): points are (x, y) tuples
# ---------------------------------------------------------------------------

def snap_value(v, step):
    if step <= 0:
        return v
    return round(v / step) * step


def snap_angle(origin, target, step_deg=15.0):
    """Rotate target around origin onto the nearest step_deg direction."""
    dx, dy = target[0] - origin[0], target[1] - origin[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return target
    step = math.radians(step_deg)
    ang = round(math.atan2(dy, dx) / step) * step
    return (origin[0] + math.cos(ang) * length, origin[1] + math.sin(ang) * length)


def lock_to_axis(origin, target, tol_deg=8.0):
    """Pull target onto the grid X or Y axis through origin when the edge is
    within tol_deg of it. Returns (point, 'X' / 'Y' / None)."""
    dx, dy = target[0] - origin[0], target[1] - origin[1]
    if math.hypot(dx, dy) < 1e-9:
        return target, None
    ang = math.degrees(math.atan2(abs(dy), abs(dx)))  # 0 = along X, 90 = along Y
    if ang <= tol_deg:
        return (target[0], origin[1]), 'X'
    if ang >= 90.0 - tol_deg:
        return (origin[0], target[1]), 'Y'
    return target, None


def cubic(p0, p1, p2, p3, t):
    u = 1.0 - t
    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
            a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def _same(a, b, eps=1e-7):
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


def segment_is_straight(a, b):
    """a, b: point dicts with co / h_in / h_out. Straight when no handles pull."""
    return _same(a["h_out"], a["co"]) and _same(b["h_in"], b["co"])


def sample_outline(points, resolution, closed=True):
    """Turn control points into a polyline.

    Returns a list of (xy, is_control, smooth) tuples. Straight segments add only
    their start point; curved ones add `resolution` samples.
    """
    out = []
    n = len(points)
    if n == 0:
        return out
    seg_count = n if closed else n - 1
    for i in range(n):
        a = points[i]
        out.append((tuple(a["co"]), True, a["smooth"]))
        if i >= seg_count:
            break
        b = points[(i + 1) % n]
        if segment_is_straight(a, b):
            continue
        res = max(2, int(resolution))
        for k in range(1, res):
            t = k / res
            out.append((cubic(a["co"], a["h_out"], b["h_in"], b["co"], t), False, True))
    return out


def signed_area(poly):
    area = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return area * 0.5


def dedupe_ring(samples, eps=1e-6):
    """Drop consecutive duplicates (including last == first)."""
    out = []
    for s in samples:
        if out and _same(out[-1][0], s[0], eps):
            continue
        out.append(s)
    while len(out) > 1 and _same(out[-1][0], out[0][0], eps):
        out.pop()
    return out


def bounds_center(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return ((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5)


def segment_length(a, b, steps=48):
    """Length of the edge from point a to point b (arc length when curved)."""
    if segment_is_straight(a, b):
        return math.dist(a["co"], b["co"])
    total, prev = 0.0, a["co"]
    for k in range(1, steps + 1):
        cur = cubic(a["co"], a["h_out"], b["h_in"], b["co"], k / steps)
        total += math.dist(prev, cur)
        prev = cur
    return total


CIRCLE_K = 0.5522847498  # cubic handle length for a quarter circle of radius 1


def rect_points(a, b, square=False):
    """Corner points of the rectangle with opposite corners a and b (a square with `square`)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if square:
        side = max(abs(dx), abs(dy))
        dx = math.copysign(side, dx or 1.0)
        dy = math.copysign(side, dy or 1.0)
    xs = (a[0], a[0] + dx)
    ys = (a[1], a[1] + dy)
    co = [(xs[0], ys[0]), (xs[1], ys[0]), (xs[1], ys[1]), (xs[0], ys[1])]
    return [{"co": c, "h_in": c, "h_out": c, "smooth": False} for c in co]


def circle_points(c, r):
    """Four smooth points (bezier quarter circles) around centre c."""
    k = CIRCLE_K * r
    cx, cy = c
    return [
        {"co": (cx + r, cy), "h_in": (cx + r, cy - k), "h_out": (cx + r, cy + k), "smooth": True},
        {"co": (cx, cy + r), "h_in": (cx + k, cy + r), "h_out": (cx - k, cy + r), "smooth": True},
        {"co": (cx - r, cy), "h_in": (cx - r, cy + k), "h_out": (cx - r, cy - k), "smooth": True},
        {"co": (cx, cy - r), "h_in": (cx - k, cy - r), "h_out": (cx + k, cy - r), "smooth": True},
    ]


def point_at_length(origin, toward, length):
    """Point `length` away from origin, heading for `toward` (+X if they coincide)."""
    dx, dy = toward[0] - origin[0], toward[1] - origin[1]
    d = math.hypot(dx, dy)
    if d < 1e-9:
        dx, dy, d = 1.0, 0.0, 1.0
    return (origin[0] + dx / d * length, origin[1] + dy / d * length)


def segment_midpoint(a, b):
    if segment_is_straight(a, b):
        return ((a["co"][0] + b["co"][0]) * 0.5, (a["co"][1] + b["co"][1]) * 0.5)
    return cubic(a["co"], a["h_out"], b["h_in"], b["co"], 0.5)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class ShapeDrawSettings(bpy.types.PropertyGroup):
    mode: EnumProperty(
        name="Shape", default='OUTLINE',
        description="What a click draws (switch while drawing with P, R and C)",
        items=(('OUTLINE', "Outline", "Click out corners, drag for curves (P)", 'IPO_BEZIER', 0),
               ('RECT', "Rectangle", "Drag from corner to corner, Shift for a square (R)", 'MESH_PLANE', 1),
               ('CIRCLE', "Circle", "Click the centre, drag out the radius (C)", 'MESH_CIRCLE', 2)))
    snap_step: FloatProperty(
        name="Grid Step", description="Snap points and height to this step (hold Ctrl to draw freely)",
        default=0.25, min=0.0, soft_max=10.0, subtype='DISTANCE', unit='LENGTH')
    use_snap: BoolProperty(name="Snap", description="Snap to the grid step", default=True)
    axis_lock: BoolProperty(
        name="Lock to Axis",
        description="Straighten an edge onto the grid's X or Y axis when it's nearly lined up, "
                    "even while Ctrl turns grid snapping off",
        default=True)
    on_surface: BoolProperty(
        name="Draw on Surfaces",
        description="Start the shape on the face under the mouse instead of the ground",
        default=True)


class ShapeDrawPoint(bpy.types.PropertyGroup):
    co: FloatVectorProperty(size=2)
    h_in: FloatVectorProperty(size=2)
    h_out: FloatVectorProperty(size=2)
    smooth: BoolProperty()


def _point_dict(p):
    return {"co": tuple(p.co), "h_in": tuple(p.h_in), "h_out": tuple(p.h_out), "smooth": p.smooth}


# ---------------------------------------------------------------------------
# Geometry building
# ---------------------------------------------------------------------------

def plane_frame(origin, normal, xaxis):
    n = Vector(normal).normalized()
    x = Vector(xaxis).normalized()
    y = n.cross(x).normalized()
    m = Matrix.Identity(4)
    m.col[0][:3] = x
    m.col[1][:3] = y
    m.col[2][:3] = n
    m.col[3][:3] = Vector(origin)
    return m


def build_mesh(name, samples, height, free_edges_only=False):
    """samples: deduped [(xy, is_control, smooth)], already shifted to the origin.

    Shading comes from face flags: caps and straight walls are flat, curved
    walls smooth (Blender keeps the edge between a flat and a smooth face
    crisp on its own). An edge is only marked sharp where two curved walls
    meet at a sharp corner, so Edit Mode isn't covered in cyan marks.

    With free_edges_only, also writes a bevel_weight_edge attribute (base rim
    0, top rim and corners 1, curved walls 0) for a Bevel modifier limited by
    weight, so the edges on the drawing surface stay sharp.
    """
    poly = [s[0] for s in samples]
    if signed_area(poly) < 0:  # make the cap wind counter-clockwise (+Z normal)
        samples = samples[::-1]
        poly = poly[::-1]
    n = len(samples)
    bm = bmesh.new()
    bottom = [bm.verts.new((x, y, 0.0)) for x, y in poly]
    if abs(height) < 1e-6:
        f = bm.faces.new(bottom)
        f.smooth = False
    else:
        top = [bm.verts.new((x, y, height)) for x, y in poly]
        cap_top = bm.faces.new(top)
        cap_bot = bm.faces.new(bottom[::-1])
        cap_top.smooth = cap_bot.smooth = False
        # Wall i runs from sample i to i+1; it's curved unless both ends are
        # control points (curved segments always have samples in between).
        curved = [not (samples[i][1] and samples[(i + 1) % n][1]) for i in range(n)]
        for i in range(n):
            j = (i + 1) % n
            side = bm.faces.new((bottom[i], bottom[j], top[j], top[i]))
            side.smooth = curved[i]
        corner = [samples[i][1] and not samples[i][2] for i in range(n)]
        for i in range(n):
            if corner[i] and curved[i] and curved[i - 1]:
                e = bm.edges.get((bottom[i], top[i]))
                if e is not None:
                    e.smooth = False
        if free_edges_only:
            weight = bm.edges.layers.float.new("bevel_weight_edge")
            for e in cap_top.edges:
                e[weight] = 1.0
            for i in range(n):
                e = bm.edges.get((bottom[i], top[i]))
                if e is not None and corner[i]:
                    e[weight] = 1.0
        if height < 0:
            bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    # Straight segments between corners should shade flat: a side quad whose
    # both vertical edges are sharp ends up flat-looking anyway.
    return me


def build_curve(name, points, height, bevel, bevel_segments, resolution):
    cu = bpy.data.curves.new(name, 'CURVE')
    cu.dimensions = '2D'
    cu.fill_mode = 'BOTH'
    cu.resolution_u = max(1, resolution)
    h = abs(height)
    b = min(bevel, h * 0.5) if h > 0 else 0.0
    cu.extrude = max(h * 0.5 - b, 0.0)
    if b > 0:
        cu.bevel_mode = 'ROUND'
        cu.bevel_depth = b
        cu.bevel_resolution = max(0, bevel_segments - 1)
        cu.offset = -b
    sp = cu.splines.new('BEZIER')
    sp.bezier_points.add(len(points) - 1)
    sp.use_cyclic_u = True
    for bp, p in zip(sp.bezier_points, points, strict=True):
        bp.co = (p["co"][0], p["co"][1], 0.0)
        if p["smooth"]:
            bp.handle_left_type = bp.handle_right_type = 'FREE'
            bp.handle_left = (p["h_in"][0], p["h_in"][1], 0.0)
            bp.handle_right = (p["h_out"][0], p["h_out"][1], 0.0)
        else:
            bp.handle_left_type = bp.handle_right_type = 'VECTOR'
    return cu


def stored_world_matrix(ob):
    """An object's world matrix rebuilt from its saved transform and parents.

    The redo panel re-runs the tool right after an undo, when Blender's
    computed matrix_world (even from the evaluated depsgraph) can still be
    identity, which put parented shapes and cutters in the wrong place.
    location/rotation/scale and parent inverses are saved data, so they're
    always right. (Constraints and bone parents aren't taken into account.)
    """
    m = ob.matrix_basis.copy()
    while ob.parent is not None:
        m = ob.matrix_parent_inverse @ m
        ob = ob.parent
        m = ob.matrix_basis @ m
    return m


def parent_keep_world(context, obj, parent, world):
    """Parent obj to parent while keeping obj at `world`."""
    parent_world = stored_world_matrix(parent)
    obj.parent = parent
    obj.matrix_parent_inverse = parent_world.inverted()
    obj.matrix_basis = world


def join_into(context, obj, target):
    """Boolean-union obj (with its modifiers) into target's own mesh, then delete obj.

    The union goes first in target's stack, so target's existing modifiers
    (bevels, mirrors...) now apply to the merged shape too.
    """
    mod = target.modifiers.new("ShapeDrawJoin", 'BOOLEAN')
    mod.operation = 'UNION'
    mod.solver = 'EXACT'
    mod.object = obj
    try:
        with context.temp_override(object=target, active_object=target,
                                   selected_objects=[target], selected_editable_objects=[target]):
            bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=0)
            bpy.ops.object.modifier_apply(modifier=mod.name)
    except RuntimeError as exc:
        print("Shape Draw join failed:", exc)
        if mod.name in target.modifiers:
            target.modifiers.remove(mod)
        return False
    me = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if me.users == 0:
        bpy.data.meshes.remove(me)
    return True


# ---------------------------------------------------------------------------
# Viewport drawing helpers
# ---------------------------------------------------------------------------

def format_length(context, value):
    """Length in the scene's units (2 m, 35 cm, 1' 4\"...), plain metres as a fallback."""
    us = context.scene.unit_settings
    if us.system != 'NONE':
        try:
            return bpy.utils.units.to_string(
                us.system, 'LENGTH', value * us.scale_length, precision=3,
                split_unit=us.use_separate, compatible_unit=False)
        except Exception:
            pass
    return f"{value:.3f}".rstrip("0").rstrip(".")


def parse_length(context, text):
    """Typed number in the scene's unit (metres, or feet for imperial). None if not a number."""
    us = context.scene.unit_settings
    try:
        value = float(text)
    except ValueError:
        return None
    if us.system != 'NONE':
        try:
            value = bpy.utils.units.to_value(us.system, 'LENGTH', text)
        except Exception:
            pass
    return value / (us.scale_length or 1.0)


COL_AXIS_X = (1.0, 0.25, 0.3, 0.6)   # Blender's red / green axis colours
COL_AXIS_Y = (0.45, 0.85, 0.2, 0.6)
COL_LINE = (1.0, 0.62, 0.15, 1.0)
COL_LINE_SOFT = (1.0, 0.62, 0.15, 0.55)
COL_FILL = (1.0, 0.62, 0.15, 0.16)
COL_POINT = (1.0, 1.0, 1.0, 1.0)
COL_FIRST = (0.35, 1.0, 0.45, 1.0)
COL_HANDLE = (0.55, 0.8, 1.0, 1.0)


def _shader(name):
    return gpu.shader.from_builtin(name)


def _draw_lines(coords, color, width=2.0, mode='LINES'):
    if len(coords) < 2:
        return
    sh = _shader('POLYLINE_UNIFORM_COLOR')
    batch = batch_for_shader(sh, mode, {"pos": coords})
    sh.bind()
    sh.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    sh.uniform_float("lineWidth", width)
    sh.uniform_float("color", color)
    batch.draw(sh)


def _draw_tris(coords, indices, color):
    if not indices:
        return
    sh = _shader('UNIFORM_COLOR')
    batch = batch_for_shader(sh, 'TRIS', {"pos": coords}, indices=indices)
    sh.bind()
    sh.uniform_float("color", color)
    batch.draw(sh)


def _draw_points(coords, color, size=8.0):
    if not coords:
        return
    sh = _shader('POINT_UNIFORM_COLOR')
    batch = batch_for_shader(sh, 'POINTS', {"pos": coords})
    gpu.state.point_size_set(size)
    sh.bind()
    sh.uniform_float("color", color)
    batch.draw(sh)


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

NUM_KEYS = {
    'ZERO': '0', 'ONE': '1', 'TWO': '2', 'THREE': '3', 'FOUR': '4',
    'FIVE': '5', 'SIX': '6', 'SEVEN': '7', 'EIGHT': '8', 'NINE': '9',
    'NUMPAD_0': '0', 'NUMPAD_1': '1', 'NUMPAD_2': '2', 'NUMPAD_3': '3',
    'NUMPAD_4': '4', 'NUMPAD_5': '5', 'NUMPAD_6': '6', 'NUMPAD_7': '7',
    'NUMPAD_8': '8', 'NUMPAD_9': '9', 'PERIOD': '.', 'NUMPAD_PERIOD': '.',
    'COMMA': '.',
}
NAV_EVENTS = {
    'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE',
    'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE', 'MOUSESMARTZOOM', 'NDOF_MOTION',
}
CLOSE_PX = 14.0
DRAG_PX = 6.0

DRAW_HINT = ("Click: corner   Drag: curve   Type: edge length   Click first point / Enter: close   "
             "Shift: 15°   Ctrl: no snap   Backspace: undo point   R / C: rectangle / circle   Esc: cancel")
RECT_HINT = ("Click or drag: corner to corner   Shift: square   Ctrl: no snap   "
             "Backspace: start again   P / C: outline / circle   Esc: cancel")
CIRCLE_HINT = ("Click: centre, then click or drag: radius   Type: exact radius   Ctrl: no snap   "
               "Backspace: start again   P / R: outline / rectangle   Esc: cancel")
MODE_HINTS = {'OUTLINE': DRAW_HINT, 'RECT': RECT_HINT, 'CIRCLE': CIRCLE_HINT}
MODE_KEYS = {'P': 'OUTLINE', 'R': 'RECT', 'C': 'CIRCLE'}
LENGTH_HINT = ("Typing a length: aim with the mouse   Click / Enter: place point   "
               "Shift: 15°   Backspace: edit   Esc: stop typing")
EXTRUDE_HINT = ("Move: height   Type: exact height   Click / Enter: finish   "
                "Ctrl: no snap   RMB: back to drawing   Esc: cancel")


class VIEW3D_OT_shape_draw(bpy.types.Operator):
    """Click out an outline (drag for curves), close it, then pull it up into a solid"""
    bl_idname = "view3d.shape_draw"
    bl_label = "Draw Shape"
    bl_options = {'REGISTER', 'UNDO'}

    height: FloatProperty(name="Height", default=1.0, subtype='DISTANCE', unit='LENGTH')
    output: EnumProperty(
        name="Result", default='MESH',
        items=(('MESH', "Mesh", "A solid mesh"),
               ('CURVE', "Curve", "An extruded curve you can keep reshaping")))
    bevel: FloatProperty(name="Bevel", default=0.0, min=0.0, soft_max=1.0,
                         subtype='DISTANCE', unit='LENGTH')
    bevel_segments: IntProperty(name="Bevel Segments", description="How round the bevel is",
                                default=2, min=1, max=32)
    bevel_free_only: BoolProperty(
        name="Only Free Edges",
        description="Keep the edges where the shape meets the surface it was drawn on sharp, "
                    "and bevel only the rest",
        default=False)
    resolution: IntProperty(name="Curve Segments",
                            description="How smooth the outline's curved edges are (segments per curve). "
                                        "Bevel roundness is Bevel Segments",
                            default=12, min=2, max=128)
    attach: EnumProperty(
        name="Attach", default='OFF',
        description="What to do with the object the shape was drawn on",
        items=(('OFF', "Off", "Make a separate object"),
               ('PARENT', "Parent", "Make the shape a child of the object it was drawn on"),
               ('JOIN', "Join", "Merge the shape into the object it was drawn on"),
               ('CUT', "Cut", "Cut the shape into the object it was drawn on (stays editable)")))

    # Captured shape (filled by the modal, replayed by the redo panel).
    target: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    points: CollectionProperty(type=ShapeDrawPoint, options={'HIDDEN', 'SKIP_SAVE'})
    plane_origin: FloatVectorProperty(size=3, options={'HIDDEN', 'SKIP_SAVE'})
    plane_normal: FloatVectorProperty(size=3, default=(0, 0, 1), options={'HIDDEN', 'SKIP_SAVE'})
    plane_xaxis: FloatVectorProperty(size=3, default=(1, 0, 0), options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D' and context.mode == 'OBJECT'

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        target = bpy.data.objects.get(self.target) if self.target else None
        col = layout.column()
        col.active = target is not None
        col.row().prop(self, "attach", expand=True)
        layout.prop(self, "height")
        row = layout.row()
        row.active = self.attach not in {'JOIN', 'CUT'} or target is None
        row.prop(self, "output")
        if self._has_curves():  # only an outline with curved edges has anything to smooth
            layout.prop(self, "resolution")
        layout.separator()
        layout.prop(self, "bevel")
        if self.bevel > 0:
            layout.prop(self, "bevel_segments")
            row = layout.row()
            row.active = self.output == 'MESH'
            row.prop(self, "bevel_free_only")

    def _has_curves(self):
        pts = [_point_dict(p) for p in self.points]
        return any(not segment_is_straight(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts)))

    # -- building ----------------------------------------------------------

    def execute(self, context):
        pts = [_point_dict(p) for p in self.points]
        if len(pts) < 3 and not (len(pts) == 2 and any(p["smooth"] for p in pts)):
            self.report({'WARNING'}, "A shape needs at least 3 points")
            return {'CANCELLED'}
        frame = plane_frame(self.plane_origin, self.plane_normal, self.plane_xaxis)
        samples = dedupe_ring(sample_outline(pts, self.resolution))
        if len(samples) < 3:
            self.report({'WARNING'}, "Shape is too small")
            return {'CANCELLED'}
        cx, cy = bounds_center([s[0] for s in samples])
        shift = lambda xy: (xy[0] - cx, xy[1] - cy)
        name = "Shape"

        target = bpy.data.objects.get(self.target) if self.target else None
        attach = self.attach if target is not None else 'OFF'
        if attach in {'JOIN', 'CUT'} and target.type != 'MESH':
            self.report({'WARNING'}, f"{target.name} isn't a mesh, so the shape was only parented")
            attach = 'PARENT'
        if attach == 'JOIN' and target.data.users > 1:
            self.report({'WARNING'},
                        f"{target.name} shares its mesh with other objects, so the shape was only parented")
            attach = 'PARENT'
        output = 'MESH' if attach in {'JOIN', 'CUT'} else self.output
        height = self.height
        lift = 0.0
        if attach == 'CUT':
            # Always cut inwards, and start the cutter a hair outside the surface
            # so the boolean never has to deal with coincident faces.
            lift = max(1e-3, abs(height) * 1e-3)
            height = -(abs(height) + lift)
            name = "Cut"

        if output == 'CURVE':
            local_pts = [{"co": shift(p["co"]), "h_in": shift(p["h_in"]),
                          "h_out": shift(p["h_out"]), "smooth": p["smooth"]} for p in pts]
            data = build_curve(name, local_pts, self.height, self.bevel,
                               self.bevel_segments, self.resolution)
            # Curve extrusion is symmetric, so lift it by half the height.
            mat = frame @ Matrix.Translation((cx, cy, self.height * 0.5))
        else:
            data = build_mesh(name, [(shift(s[0]), s[1], s[2]) for s in samples], height,
                              free_edges_only=self.bevel_free_only)
            mat = frame @ Matrix.Translation((cx, cy, lift))

        obj = bpy.data.objects.new(name, data)
        context.collection.objects.link(obj)
        obj.matrix_world = mat
        if output == 'MESH' and self.bevel > 0 and abs(height) > 1e-6:
            mod = obj.modifiers.new("Bevel", 'BEVEL')
            mod.width = self.bevel
            mod.segments = self.bevel_segments
            if self.bevel_free_only:
                # Edge weights from build_mesh: base rim 0, everything else 1.
                mod.limit_method = 'WEIGHT'
            else:
                mod.limit_method = 'ANGLE'
            mod.harden_normals = False

        result = obj
        if attach == 'PARENT':
            parent_keep_world(context, obj, target, mat)
        elif attach == 'CUT':
            parent_keep_world(context, obj, target, mat)
            obj.display_type = 'WIRE'
            obj.hide_render = True
            mod = target.modifiers.new("Cut", 'BOOLEAN')
            mod.operation = 'DIFFERENCE'
            mod.solver = 'EXACT'
            mod.object = obj
            result = target
        elif attach == 'JOIN':
            if join_into(context, obj, target):
                result = target
            else:
                self.report({'WARNING'}, "Couldn't merge the shape, so it was parented instead")
                parent_keep_world(context, obj, target, mat)

        for o in context.selected_objects:
            o.select_set(False)
        result.select_set(True)
        context.view_layer.objects.active = result
        if attach == 'CUT':
            obj.hide_set(True)  # out of the way (and out of the next ray cast)
        return {'FINISHED'}

    # -- modal state helpers ---------------------------------------------

    def _settings(self, context):
        return context.scene.shape_draw

    def _ray(self, context, mouse):
        region, rv3d = context.region, context.region_data
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouse)
        return origin, direction

    def _pick_plane(self, context, mouse):
        """Plane under the mouse: a face (if enabled and hit) or the ground.

        Returns (plane, face, object) where face is the hit polygon's
        world-space corners (for the highlight); face and object are None on
        the ground.
        """
        s = self._settings(context)
        origin, direction = self._ray(context, mouse)
        if s.on_surface:
            dg = context.evaluated_depsgraph_get()
            hit, loc, nor, idx, ob, mat = context.scene.ray_cast(dg, origin, direction)
            if hit:
                n = nor.normalized()
                if n.dot(direction) > 0:
                    n = -n
                return self._make_plane(loc, n), self._face_coords(dg, ob, idx, mat), ob.original
        return self._make_plane(Vector((0, 0, 0)), Vector((0, 0, 1))), None, None

    def _face_coords(self, dg, ob, idx, mat):
        key = (ob.name, idx, tuple(tuple(r) for r in mat))
        if getattr(self, "_face_cache", (None,))[0] == key:
            return self._face_cache[1]
        coords = None
        ob_eval = ob.evaluated_get(dg)
        try:
            me = ob_eval.to_mesh()
            if 0 <= idx < len(me.polygons):
                coords = [mat @ me.vertices[v].co for v in me.polygons[idx].vertices]
            ob_eval.to_mesh_clear()
        except RuntimeError:
            pass
        self._face_cache = (key, coords)
        return coords

    @staticmethod
    def _make_plane(point, normal):
        n = Vector(normal).normalized()
        # Align the in-plane X with world X where possible so the grid lines up.
        for ref in (Vector((1, 0, 0)), Vector((0, 1, 0))):
            x = ref - n * ref.dot(n)
            if x.length > 1e-3:
                break
        x.normalize()
        # Plane origin = closest point to world origin, so snapping matches the world grid.
        origin = n * Vector(point).dot(n)
        return origin, n, x

    def _to_local(self, world, plane=None):
        o, n, x = plane or self.plane
        y = n.cross(x)
        d = world - o
        return (d.dot(x), d.dot(y))

    def _to_world(self, xy, plane=None):
        o, n, x = plane or self.plane
        y = n.cross(x)
        return o + x * xy[0] + y * xy[1]

    def _mouse_local(self, context, mouse, event, plane=None):
        o, n, _x = plane or self.plane
        origin, direction = self._ray(context, mouse)
        hit = geometry.intersect_line_plane(origin, origin + direction, o, n)
        if hit is None:
            return None
        xy = self._to_local(hit, plane)
        s = self._settings(context)
        if s.use_snap and not event.ctrl:
            xy = (snap_value(xy[0], s.snap_step), snap_value(xy[1], s.snap_step))
        return xy

    def _to_px(self, context, world):
        return view3d_utils.location_3d_to_region_2d(context.region, context.region_data, world)

    def _near_first(self, context, mouse):
        if len(self.pts) < 3 and not (len(self.pts) == 2 and any(p["smooth"] for p in self.pts)):
            return False
        px = self._to_px(context, self._to_world(self.pts[0]["co"]))
        return px is not None and (px - Vector(mouse)).length < CLOSE_PX

    def _hover_point(self, context, event):
        """Where the next point would go (with Shift angle lock)."""
        mouse = (event.mouse_region_x, event.mouse_region_y)
        if self.plane:
            plane = self.plane
        else:
            plane, self.hover_face, self.hover_target = self._pick_plane(context, mouse)
        xy = self._mouse_local(context, mouse, event, plane)
        if xy is None:
            return None, plane
        self.axis = None
        if self.mode != 'OUTLINE':
            return xy, plane
        if event.shift and self.pts:
            xy = snap_angle(self.pts[-1]["co"], xy)
        elif self.pts and self._settings(context).axis_lock:
            xy, self.axis = lock_to_axis(self.pts[-1]["co"], xy)
        return xy, plane

    def _typed_point(self, context, event):
        """Next point at the typed length, aimed from the last point at the mouse."""
        length = parse_length(context, self.len_typed)
        if length is None or not self.pts or self.plane is None:
            return None
        last = self.pts[-1]["co"]
        mouse = (event.mouse_region_x, event.mouse_region_y)
        o, n, _x = self.plane
        origin, direction = self._ray(context, mouse)
        hit = geometry.intersect_line_plane(origin, origin + direction, o, n)
        if hit is None:
            return None
        aim = self._to_local(hit)
        self.axis = None
        if event.shift:
            aim = snap_angle(last, aim)
        elif self._settings(context).axis_lock:
            aim, self.axis = lock_to_axis(last, aim)
        return point_at_length(last, aim, length)

    def _set_len_typed(self, context, event, text):
        self.len_typed = text
        if text:
            context.area.header_text_set(f"Edge length: {text}")
            context.workspace.status_text_set(LENGTH_HINT)
        else:
            context.area.header_text_set(None)
            context.workspace.status_text_set(MODE_HINTS[self.mode])
        self._refresh_hover(context, event)

    def _refresh_hover(self, context, event):
        self.hover, self.hover_plane = self._hover_point(context, event)
        if self.len_typed:
            typed = self._typed_point(context, event)
            if typed is not None:
                self.hover = typed
            self.closing = False
        else:
            self.closing = self._near_first(context, (event.mouse_region_x, event.mouse_region_y))

    def _place_typed(self, context, event):
        xy = self._typed_point(context, event)
        if xy is not None and not _same(xy, self.pts[-1]["co"]):
            self.pts.append({"co": xy, "h_in": xy, "h_out": xy, "smooth": False})
        self._set_len_typed(context, event, "")

    # -- quick shapes (rectangle / circle) ----------------------------------

    def _set_mode(self, context, mode):
        self.mode = mode
        self._settings(context).mode = mode
        self._reset_shape(context)

    def _reset_shape(self, context):
        self.pts = []
        self.plane = None
        self.locked_face = None
        self.locked_target = ""
        self.q_anchor = None
        self.q_end = None
        self.len_typed = ""
        context.area.header_text_set(None)
        context.workspace.status_text_set(MODE_HINTS[self.mode])

    def _quick_radius(self, context, event):
        if self.len_typed:
            return parse_length(context, self.len_typed) or 0.0
        r = math.dist(self.q_anchor, self.q_end)
        s = self._settings(context)
        if s.use_snap and not event.ctrl and s.snap_step > 0:
            r = max(s.snap_step, snap_value(r, s.snap_step))
        return r

    def _quick_update(self, context, event):
        """Rebuild the rectangle / circle from the anchor and the mouse."""
        xy, plane = self._hover_point(context, event)
        if self.q_anchor is None:
            self.hover, self.hover_plane = xy, plane
            return
        self.hover = None
        if xy is not None:
            self.q_end = xy
        if self.q_end is None:
            return
        if self.mode == 'RECT':
            self.pts = rect_points(self.q_anchor, self.q_end, square=event.shift)
        else:
            self.pts = circle_points(self.q_anchor, self._quick_radius(context, event))

    def _quick_valid(self):
        if len(self.pts) < 3:
            return False
        xs = [p["co"][0] for p in self.pts]
        ys = [p["co"][1] for p in self.pts]
        return max(xs) - min(xs) > 1e-6 and max(ys) - min(ys) > 1e-6

    def _quick_press(self, context, event):
        mouse = (event.mouse_region_x, event.mouse_region_y)
        if self.q_anchor is not None:  # second click sets the size
            self._quick_update(context, event)
            if self._quick_valid():
                self._start_extrude(context, event)
            return
        xy, plane = self._hover_point(context, event)
        if xy is None:
            return
        self.plane = plane
        self.locked_face = self.hover_face
        self.locked_target = self.hover_target.name if self.hover_target else ""
        self.q_anchor = xy
        self.q_end = xy
        self.hover = None
        self.lmb_down = True
        self.press_px = Vector(mouse)

    def _modal_quick(self, context, event):
        t, v = event.type, event.value
        if t == 'MOUSEMOVE' or t in {'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_CTRL', 'RIGHT_CTRL'}:
            self._quick_update(context, event)
        elif t == 'LEFTMOUSE' and v in {'PRESS', 'DOUBLE_CLICK'}:
            self._quick_press(context, event)
        elif t == 'LEFTMOUSE' and v == 'RELEASE':
            dragged = (self.press_px is not None and self.lmb_down and
                       (Vector((event.mouse_region_x, event.mouse_region_y)) - self.press_px).length >= DRAG_PX)
            self.lmb_down = False
            if dragged and self.q_anchor is not None:  # drag-and-release also sets the size
                self._quick_update(context, event)
                if self._quick_valid():
                    self._start_extrude(context, event)
        elif v == 'PRESS' and t in NUM_KEYS and self.mode == 'CIRCLE' and self.q_anchor is not None:
            if not (NUM_KEYS[t] == '.' and '.' in self.len_typed):
                self.len_typed += NUM_KEYS[t]
                context.area.header_text_set(f"Radius: {self.len_typed}")
                self._quick_update(context, event)
        elif v == 'PRESS' and t == 'BACK_SPACE' and self.len_typed:
            self.len_typed = self.len_typed[:-1]
            context.area.header_text_set(f"Radius: {self.len_typed}" if self.len_typed else None)
            self._quick_update(context, event)
        elif v == 'PRESS' and (t == 'BACK_SPACE' or (t == 'Z' and event.ctrl)):
            self._reset_shape(context)
            self._quick_update(context, event)
        elif t in {'RET', 'NUMPAD_ENTER', 'SPACE'} and v == 'PRESS':
            if self.q_anchor is not None:
                self._quick_update(context, event)
                if self._quick_valid():
                    self._start_extrude(context, event)
        elif t == 'RIGHTMOUSE' and v == 'PRESS':
            self._cleanup(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    # -- height --------------------------------------------------------------

    def _raw_height(self, context, mouse):
        o, n, _x = self.plane
        anchor = self._to_world(self.anchor_xy)
        origin, direction = self._ray(context, mouse)
        if abs(direction.normalized().dot(n)) < 0.985:
            res = geometry.intersect_line_line(anchor, anchor + n, origin, origin + direction)
            if res is not None:
                return (res[0] - anchor).dot(n)
        # Looking straight down the normal: use vertical mouse travel instead.
        p0 = view3d_utils.region_2d_to_location_3d(context.region, context.region_data, (0, 0), anchor)
        p1 = view3d_utils.region_2d_to_location_3d(context.region, context.region_data, (0, 1), anchor)
        return mouse[1] * (p1 - p0).length

    def _update_height(self, context, event):
        if self.typed:
            try:
                h = float(self.typed)
            except ValueError:
                return
        else:
            mouse = (event.mouse_region_x, event.mouse_region_y)
            h = self._raw_height(context, mouse) - self.height_zero
            s = self._settings(context)
            if s.use_snap and not event.ctrl:
                h = snap_value(h, s.snap_step)
        if self.attach == 'CUT' and self.locked_target:
            h = -abs(h)  # a cut always goes into the surface, whichever way you drag
        self.cur_height = h

    # -- modal -----------------------------------------------------------

    def invoke(self, context, event):
        if context.space_data.type != 'VIEW_3D':
            return {'CANCELLED'}
        self.pts = []
        self.plane = None
        self.hover = None
        self.hover_plane = None
        self.hover_face = None
        self.locked_face = None
        self.hover_target = None
        self.locked_target = ""
        self.stage = 'DRAW'
        self.lmb_down = False
        self.press_px = None
        self.typed = ""
        self.len_typed = ""
        self.cur_height = 0.0
        self.anchor_xy = (0.0, 0.0)
        self.height_zero = 0.0
        self.closing = False
        self.axis = None
        self.mode = self._settings(context).mode
        self.q_anchor = None
        self.q_end = None

        args = (context,)
        self._h3d = bpy.types.SpaceView3D.draw_handler_add(self._draw_3d, args, 'WINDOW', 'POST_VIEW')
        self._h2d = bpy.types.SpaceView3D.draw_handler_add(self._draw_2d, args, 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(MODE_HINTS[self.mode])

        # Started by a click (toolbar tool): that click is the first point.
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if self.mode == 'OUTLINE':
                self._press(context, event)
            else:
                self._quick_press(context, event)
        else:
            self.hover, self.hover_plane = self._hover_point(context, event)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def _cleanup(self, context):
        if getattr(self, "_h3d", None):
            bpy.types.SpaceView3D.draw_handler_remove(self._h3d, 'WINDOW')
            self._h3d = None
        if getattr(self, "_h2d", None):
            bpy.types.SpaceView3D.draw_handler_remove(self._h2d, 'WINDOW')
            self._h2d = None
        context.workspace.status_text_set(None)
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def _press(self, context, event):
        mouse = (event.mouse_region_x, event.mouse_region_y)
        if self._near_first(context, mouse):
            self._start_extrude(context, event)
            return
        xy, plane = self._hover_point(context, event)
        if xy is None:
            return
        if self.plane is None:
            self.plane = plane
            self.locked_face = self.hover_face
            self.locked_target = self.hover_target.name if self.hover_target else ""
        if self.pts and _same(self.pts[-1]["co"], xy):
            # Clicking the last point again: allow dragging out its handles.
            self.lmb_down = True
            self.press_px = Vector(mouse)
            return
        self.pts.append({"co": xy, "h_in": xy, "h_out": xy, "smooth": False})
        self.lmb_down = True
        self.press_px = Vector(mouse)

    def _drag(self, context, event):
        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        if not self.pts or (mouse - self.press_px).length < DRAG_PX:
            return
        xy = self._mouse_local(context, mouse, event)
        if xy is None:
            return
        p = self.pts[-1]
        co = p["co"]
        p["h_out"] = xy
        p["h_in"] = (2 * co[0] - xy[0], 2 * co[1] - xy[1])
        p["smooth"] = not _same(xy, co)

    def _start_extrude(self, context, event):
        self.len_typed = ""
        context.area.header_text_set(None)
        self.stage = 'EXTRUDE'
        self.lmb_down = False
        samples = sample_outline(self.pts, 12)
        self.anchor_xy = bounds_center([s[0] for s in samples])
        mouse = (event.mouse_region_x, event.mouse_region_y)
        self.height_zero = self._raw_height(context, mouse)
        self.cur_height = 0.0
        self.typed = ""
        context.workspace.status_text_set(EXTRUDE_HINT)

    def _finish(self, context):
        self._cleanup(context)
        self.points.clear()
        for p in self.pts:
            item = self.points.add()
            item.co, item.h_in, item.h_out, item.smooth = p["co"], p["h_in"], p["h_out"], p["smooth"]
        o, n, x = self.plane
        self.plane_origin, self.plane_normal, self.plane_xaxis = o, n, x
        self.height = self.cur_height
        self.target = self.locked_target
        return self.execute(context)

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception:
            self._cleanup(context)
            raise

    def _modal(self, context, event):
        context.area.tag_redraw()
        t, v = event.type, event.value

        typing_len = self.stage == 'DRAW' and bool(self.len_typed)
        if t in NAV_EVENTS or (t.startswith('NUMPAD_') and self.stage == 'DRAW'
                               and t != 'NUMPAD_ENTER' and not (typing_len and t in NUM_KEYS)):
            return {'PASS_THROUGH'}

        if t == 'ESC' and v == 'PRESS' and typing_len:
            self._set_len_typed(context, event, "")
            return {'RUNNING_MODAL'}
        if t == 'ESC' and v == 'PRESS':
            self._cleanup(context)
            return {'CANCELLED'}

        if self.stage == 'DRAW' and v == 'PRESS' and t in MODE_KEYS and not typing_len and not self.lmb_down:
            self._set_mode(context, MODE_KEYS[t])
            if self.mode == 'OUTLINE':
                self._refresh_hover(context, event)
            else:
                self._quick_update(context, event)
            return {'RUNNING_MODAL'}
        if self.stage == 'DRAW' and self.mode != 'OUTLINE':
            return self._modal_quick(context, event)

        if self.stage == 'DRAW':
            if t == 'MOUSEMOVE' or (t in {'LEFT_SHIFT', 'RIGHT_SHIFT'} and typing_len):
                if self.lmb_down:
                    self._drag(context, event)
                self._refresh_hover(context, event)
            elif typing_len and v == 'PRESS' and t in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'SPACE'}:
                self._place_typed(context, event)
            elif v == 'PRESS' and t in NUM_KEYS and self.pts and not self.lmb_down:
                if not (NUM_KEYS[t] == '.' and '.' in self.len_typed):
                    self._set_len_typed(context, event, self.len_typed + NUM_KEYS[t])
            elif v == 'PRESS' and t == 'BACK_SPACE' and typing_len:
                self._set_len_typed(context, event, self.len_typed[:-1])
            elif t == 'LEFTMOUSE':
                if v in {'PRESS', 'DOUBLE_CLICK'}:
                    self._press(context, event)
                elif v == 'RELEASE':
                    self.lmb_down = False
            elif t in {'RET', 'NUMPAD_ENTER', 'SPACE'} and v == 'PRESS':
                n = len(self.pts)
                if n >= 3 or (n == 2 and any(p["smooth"] for p in self.pts)):
                    self._start_extrude(context, event)
            elif v == 'PRESS' and (t == 'BACK_SPACE' or (t == 'Z' and event.ctrl)):
                if self.pts:
                    self.pts.pop()
                    if not self.pts:
                        self.plane = None
                        self.locked_face = None
                        self.locked_target = ""
            elif t == 'RIGHTMOUSE' and v == 'PRESS':
                self._cleanup(context)
                return {'CANCELLED'}
            return {'RUNNING_MODAL'}

        # EXTRUDE stage
        if t == 'MOUSEMOVE':
            self._update_height(context, event)
        elif t in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'SPACE'} and v == 'PRESS':
            return self._finish(context)
        elif t == 'RIGHTMOUSE' and v == 'PRESS':
            self.stage = 'DRAW'
            self.typed = ""
            context.area.header_text_set(None)
            context.workspace.status_text_set(MODE_HINTS[self.mode])
        elif v == 'PRESS' and t in NUM_KEYS:
            self.typed += NUM_KEYS[t]
            self._update_height(context, event)
        elif v == 'PRESS' and t in {'MINUS', 'NUMPAD_MINUS'}:
            self.typed = self.typed[1:] if self.typed.startswith('-') else '-' + self.typed
            self._update_height(context, event)
        elif v == 'PRESS' and t == 'BACK_SPACE':
            self.typed = self.typed[:-1]
            self._update_height(context, event)
        elif t in {'LEFT_CTRL', 'RIGHT_CTRL'}:
            self._update_height(context, event)

        if self.stage == 'EXTRUDE':
            shown = self.typed if self.typed else f"{self.cur_height:.3f}"
            context.area.header_text_set(f"Height: {shown}")
        return {'RUNNING_MODAL'}

    # -- viewport overlay ------------------------------------------------

    def _preview_points(self):
        pts = [dict(p) for p in self.pts]
        if self.stage == 'DRAW' and self.hover is not None and not self.lmb_down and not self.closing:
            if not pts or not _same(pts[-1]["co"], self.hover):
                pts.append({"co": self.hover, "h_in": self.hover, "h_out": self.hover, "smooth": False})
        return pts

    def _draw_3d(self, context):
        try:
            self._draw_3d_impl(context)
        except Exception as exc:  # never let a draw error kill the viewport
            print("Shape Draw overlay error:", exc)

    def _draw_3d_impl(self, context):
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        try:
            self._draw_grid(context)
            self._draw_face_highlight()
            plane = self.plane or self.hover_plane
            if plane is not None:
                self._draw_shape(plane)
        finally:
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('LESS_EQUAL')

    def _draw_grid(self, context):
        """Snap grid on the drawing plane, covering the face plus what's drawn."""
        if self.stage != 'DRAW':
            return
        s = self._settings(context)
        plane = self.plane or self.hover_plane
        if plane is None or not s.use_snap or s.snap_step <= 0:
            return
        face = self.locked_face if self.plane else self.hover_face
        pts2d = [p["co"] for p in self.pts]
        if self.hover is not None:
            pts2d.append(self.hover)
        step = s.snap_step
        if face:
            pts2d += [self._to_local(v, plane) for v in face]
            pad = step * 2
        else:  # ground: a patch around the cursor and the shape
            pad = max(step * 8, 1.0)
        if not pts2d:
            return
        xs = [p[0] for p in pts2d]
        ys = [p[1] for p in pts2d]
        x0, x1 = min(xs) - pad, max(xs) + pad
        y0, y1 = min(ys) - pad, max(ys) + pad
        # Keep the line count sane on huge faces / tiny steps.
        mult = max(1, math.ceil(max(x1 - x0, y1 - y0) / step / 160))
        g = step * mult
        i0, i1 = math.floor(x0 / g), math.ceil(x1 / g)
        j0, j1 = math.floor(y0 / g), math.ceil(y1 / g)
        x0, x1, y0, y1 = i0 * g, i1 * g, j0 * g, j1 * g
        minor, major = [], []
        w = lambda x, y: self._to_world((x, y), plane)
        for i in range(i0, i1 + 1):
            (major if i % 4 == 0 else minor).extend((w(i * g, y0), w(i * g, y1)))
        for j in range(j0, j1 + 1):
            (major if j % 4 == 0 else minor).extend((w(x0, j * g), w(x1, j * g)))
        alpha = 1.0 if self.plane else 0.6
        _draw_lines(minor, (1.0, 1.0, 1.0, 0.10 * alpha), 1.0)
        _draw_lines(major, (1.0, 1.0, 1.0, 0.25 * alpha), 1.0)

    def _draw_face_highlight(self):
        """Outline the face the shape will be drawn on (hovered, then locked)."""
        if self.stage != 'DRAW':
            return
        face = self.locked_face if self.plane else self.hover_face
        if not face or len(face) < 3:
            return
        locked = self.plane is not None
        try:
            tris = geometry.tessellate_polygon([face])
        except Exception:
            tris = []
        _draw_tris(face, list(tris), (*COL_HANDLE[:3], 0.06 if locked else 0.14))
        loop = []
        for i in range(len(face)):
            loop += [face[i], face[(i + 1) % len(face)]]
        _draw_lines(loop, (*COL_HANDLE[:3], 0.35 if locked else 0.9), 1.5 if locked else 2.5)

    def _draw_shape(self, plane):
        pts = self._preview_points()
        h_vec = plane[1] * (self.cur_height if self.stage == 'EXTRUDE' else 0.0)
        to_w = lambda xy: self._to_world(xy, plane)

        if len(pts) >= 2:
            closed = self.stage == 'EXTRUDE' or len(pts) >= 3
            samples = sample_outline(pts, 16, closed=True)
            ring = [to_w(s[0]) for s in samples]
            open_samples = sample_outline(pts, 16, closed=False)
            open_line = [to_w(s[0]) for s in open_samples] + [to_w(pts[-1]["co"])]

            # translucent fill
            if closed and len(ring) >= 3:
                poly2d = [s[0] for s in samples]
                try:
                    tris = geometry.tessellate_polygon([[Vector((x, y, 0)) for x, y in poly2d]])
                except Exception:
                    tris = []
                _draw_tris(ring, list(tris), COL_FILL)
                if self.stage == 'EXTRUDE':
                    top = [p + h_vec for p in ring]
                    _draw_tris(top, list(tris), COL_FILL)
                    walls, idx = [], []
                    for i in range(len(ring)):
                        j = (i + 1) % len(ring)
                        k = len(walls)
                        walls += [ring[i], ring[j], top[j], top[i]]
                        idx += [(k, k + 1, k + 2), (k, k + 2, k + 3)]
                    _draw_tris(walls, idx, COL_FILL)

            if self.stage == 'EXTRUDE':
                loop = []
                for i in range(len(ring)):
                    loop += [ring[i], ring[(i + 1) % len(ring)]]
                _draw_lines(loop, COL_LINE, 2.0)
                _draw_lines([v for p in loop for v in [p + h_vec]], COL_LINE, 2.0)
                verts = []
                for s, w in zip(samples, ring, strict=True):
                    if s[1]:
                        verts += [w, w + h_vec]
                _draw_lines(verts, COL_LINE, 2.0)
            elif self.mode != 'OUTLINE':  # quick shapes are always closed
                loop = []
                for i in range(len(ring)):
                    loop += [ring[i], ring[(i + 1) % len(ring)]]
                _draw_lines(loop, COL_LINE, 2.5)
            else:
                _draw_lines(open_line, COL_LINE, 2.5, 'LINE_STRIP')
                if len(pts) >= 3:  # closing edge, faint
                    tail = sample_outline([pts[-1], pts[0]], 16, closed=False)
                    _draw_lines([to_w(s[0]) for s in tail] + [to_w(pts[0]["co"])],
                                COL_LINE_SOFT, 1.5, 'LINE_STRIP')

        axis = getattr(self, "axis", None)
        if (self.stage == 'DRAW' and axis and self.pts and self.hover is not None
                and not self.lmb_down and not self.closing):
            o = self.pts[-1]["co"]
            reach = max(4.0, math.dist(o, self.hover) * 3)
            d = (reach, 0.0) if axis == 'X' else (0.0, reach)
            col = COL_AXIS_X if axis == 'X' else COL_AXIS_Y
            _draw_lines([to_w((o[0] - d[0], o[1] - d[1])), to_w((o[0] + d[0], o[1] + d[1]))], col, 1.5)

        if self.stage == 'DRAW' and self.mode != 'OUTLINE':
            if self.q_anchor is not None:
                if self.mode == 'CIRCLE' and self.pts:
                    _draw_lines([to_w(self.q_anchor), to_w(self.pts[0]["co"])], COL_LINE_SOFT, 1.5)
                _draw_points([to_w(self.q_anchor)], COL_FIRST, 9.0)
            elif self.hover is not None:
                _draw_points([to_w(self.hover)], COL_LINE, 7.0)
        elif self.stage == 'DRAW':
            # handles
            hl = []
            hp = []
            for p in self.pts:
                if p["smooth"]:
                    hl += [to_w(p["h_in"]), to_w(p["h_out"])]
                    hp += [to_w(p["h_in"]), to_w(p["h_out"])]
            _draw_lines(hl, COL_HANDLE, 1.5)
            _draw_points(hp, COL_HANDLE, 6.0)
            corners = [to_w(p["co"]) for p in self.pts[1:]]
            _draw_points(corners, COL_POINT, 8.0)
            if self.pts:
                _draw_points([to_w(self.pts[0]["co"])], COL_FIRST, 13.0 if self.closing else 9.0)
            if self.hover is not None and not self.closing and not self.lmb_down:
                _draw_points([to_w(self.hover)], COL_LINE, 7.0)

    def _draw_lengths(self, context):
        """Length label at the middle of every edge, the one being drawn brightest."""
        if self.stage != 'DRAW':
            return
        plane = self.plane or self.hover_plane
        pts = self._preview_points()
        if plane is None or len(pts) < 2:
            return
        live = len(pts) > len(self.pts) or self.lmb_down  # last edge follows the mouse
        edges = [(i, i + 1) for i in range(len(pts) - 1)]
        if len(pts) >= 3:
            edges.append((len(pts) - 1, 0))  # closing edge
        if self.mode == 'CIRCLE':
            self._draw_radius(context, plane)
            return
        if self.mode == 'RECT':
            edges, live = [(0, 1), (1, 2)], False  # width and height once each
        font = 0
        blf.enable(font, blf.SHADOW)
        blf.shadow(font, 3, 0, 0, 0, 0.9)
        try:
            for i, j in edges:
                a, b = pts[i], pts[j]
                length = segment_length(a, b)
                if length < 1e-6:
                    continue
                px = self._to_px(context, self._to_world(segment_midpoint(a, b), plane))
                if px is None:
                    continue
                if live and j == len(pts) - 1:
                    size, col = 15, (1.0, 1.0, 1.0, 1.0)
                elif j == 0:
                    size, col = 12, (1.0, 1.0, 1.0, 0.5)
                else:
                    size, col = 12, (1.0, 0.85, 0.6, 0.9)
                text = format_length(context, length)
                if live and j == len(pts) - 1 and getattr(self, "len_typed", ""):
                    text = self.len_typed + "_"
                    col = (1.0, 0.85, 0.3, 1.0)
                blf.size(font, size)
                w, h = blf.dimensions(font, text)
                blf.color(font, *col)
                blf.position(font, px.x - w / 2, px.y + 6, 0)
                blf.draw(font, text)
        finally:
            blf.disable(font, blf.SHADOW)

    def _draw_radius(self, context, plane):
        if self.q_anchor is None or not self.pts:
            return
        a, b = self.q_anchor, self.pts[0]["co"]
        px = self._to_px(context, self._to_world(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), plane))
        if px is None:
            return
        r = math.dist(a, b)
        text = f"r {self.len_typed}_" if self.len_typed else f"r {format_length(context, r)}"
        font = 0
        blf.enable(font, blf.SHADOW)
        blf.shadow(font, 3, 0, 0, 0, 0.9)
        blf.size(font, 15)
        w, _h = blf.dimensions(font, text)
        blf.color(font, *((1.0, 0.85, 0.3, 1.0) if self.len_typed else (1.0, 1.0, 1.0, 1.0)))
        blf.position(font, px.x - w / 2, px.y + 6, 0)
        blf.draw(font, text)
        blf.disable(font, blf.SHADOW)

    def _draw_2d(self, context):
        try:
            self._draw_lengths(context)
            if self.stage != 'EXTRUDE' or not self.pts:
                return
            plane = self.plane
            anchor = self._to_world(self.anchor_xy) + plane[1] * self.cur_height
            px = self._to_px(context, anchor)
            if px is None:
                return
            text = self.typed + "_" if self.typed else f"{self.cur_height:.2f} m"
            font = 0
            blf.size(font, 16)
            w, _h = blf.dimensions(font, text)
            blf.color(font, 1, 1, 1, 1)
            blf.enable(font, blf.SHADOW)
            blf.shadow(font, 3, 0, 0, 0, 0.8)
            blf.position(font, px.x - w / 2, px.y + 14, 0)
            blf.draw(font, text)
            blf.disable(font, blf.SHADOW)
        except Exception as exc:
            print("Shape Draw text error:", exc)


# ---------------------------------------------------------------------------
# Toolbar tool and menu
# ---------------------------------------------------------------------------

class ShapeDrawTool(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = "shape_draw.tool"
    bl_label = "Draw Shape"
    bl_description = ("Click out an outline (drag for curves), or drag a rectangle or circle, "
                      "then pull it up into a solid")
    bl_icon = "ops.curve.pen"
    bl_widget = None
    bl_keymap = (
        ("view3d.shape_draw", {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
    )

    def draw_settings(context, layout, tool):
        s = context.scene.shape_draw
        layout.row(align=True).prop(s, "mode", expand=True, icon_only=True)
        row = layout.row(align=True)
        row.prop(s, "use_snap", text="", icon='SNAP_ON' if s.use_snap else 'SNAP_OFF')
        sub = row.row(align=True)
        sub.active = s.use_snap
        sub.prop(s, "snap_step", text="Step")
        layout.prop(s, "axis_lock")
        layout.prop(s, "on_surface")
        props = tool.operator_properties("view3d.shape_draw")
        layout.prop(props, "attach", text="Attach")
        layout.prop(props, "output", text="")
        layout.prop(props, "bevel")


def menu_add(self, context):
    self.layout.operator(VIEW3D_OT_shape_draw.bl_idname, text="Draw Shape", icon='CURVE_BEZCURVE')


_classes = (ShapeDrawSettings, ShapeDrawPoint, VIEW3D_OT_shape_draw)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.shape_draw = PointerProperty(type=ShapeDrawSettings)
    bpy.types.VIEW3D_MT_add.append(menu_add)
    bpy.utils.register_tool(ShapeDrawTool, after={"builtin.measure"}, separator=True)


def unregister():
    bpy.utils.unregister_tool(ShapeDrawTool)
    bpy.types.VIEW3D_MT_add.remove(menu_add)
    del bpy.types.Scene.shape_draw
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
