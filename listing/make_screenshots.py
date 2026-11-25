"""Store screenshots: opens its own Blender window, stages each shot, saves it, quits.

    blender --factory-startup --window-geometry 0 0 1920 1080 --python listing/make_screenshots.py

Writes listing/screenshots/0N-*.png (2:1, 1920 x 960: Superhive's gallery ratio),
listing/previews_16x9/0N-*.png (16:9, 1920 x 1080: extensions.blender.org previews), listing/featured.png (16:9, 1920 x 1080, extensions.blender.org)
and listing/featured.jpg (2:1, Superhive). Only the add-on's own overlay is drawn: there is
no Blender logo in any shot. Your own Blender session and files aren't touched.
"""
import os
import sys
from types import SimpleNamespace as NS

import bpy
import numpy as np
from mathutils import Euler, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import shape_draw as sd  # noqa: E402

sd.register()
SHOTS = os.path.join(HERE, "screenshots")
PREVIEWS = os.path.join(HERE, "previews_16x9")
os.makedirs(SHOTS, exist_ok=True)
os.makedirs(PREVIEWS, exist_ok=True)

GROUND = (Vector((0, 0, 0)), Vector((0, 0, 1)), Vector((1, 0, 0)))
K = 0.5523  # cubic handle length for a quarter circle


def corner(x, y):
    return {"co": (x, y), "h_in": (x, y), "h_out": (x, y), "smooth": False}


def smooth(x, y, hin, hout):
    return {"co": (x, y), "h_in": hin, "h_out": hout, "smooth": True}


def circle(cx, cy, r):
    k = K * r
    return [smooth(cx + r, cy, (cx + r, cy - k), (cx + r, cy + k)),
            smooth(cx, cy + r, (cx + k, cy + r), (cx - k, cy + r)),
            smooth(cx - r, cy, (cx - r, cy + k), (cx - r, cy - k)),
            smooth(cx, cy - r, (cx - k, cy - r), (cx + k, cy - r))]


# Plan view of an arch: square front, round back.
ARCH = [corner(0, 0), corner(3, 0), corner(3, 1.5),
        smooth(1.5, 3, (1.5 + 1.5 * K, 3), (1.5 - 1.5 * K, 3)),
        corner(0, 1.5)]


# -- helpers ---------------------------------------------------------------

def ui():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region


def clear_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for me in list(bpy.data.meshes):
        bpy.data.meshes.remove(me)


def build(points, height, origin=(0, 0, 0), normal=(0, 0, 1), xaxis=(1, 0, 0), bevel=0.0,
          free_only=True, attach='OFF', target=''):
    """Run the operator's execute() as the redo panel would."""
    f = NS(points=[NS(**p) for p in points], plane_origin=origin, plane_normal=normal,
           plane_xaxis=xaxis, height=height, output='MESH', bevel=bevel, bevel_segments=3,
           bevel_free_only=free_only, resolution=24, attach=attach, target=target,
           report=lambda lvl, msg: print("REPORT", lvl, msg))
    win, area, region = ui()
    with bpy.context.temp_override(window=win, area=area, region=region):
        sd.VIEW3D_OT_shape_draw.execute(f, bpy.context)
    return bpy.context.view_layer.objects.active


def box(name, size, loc):
    me = bpy.data.meshes.new(name)
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    bpy.context.collection.objects.link(ob)
    return ob


def view(loc, dist, rot):
    _win, area, _region = ui()
    r3 = area.spaces.active.region_3d
    r3.view_perspective = 'PERSP'
    r3.view_location = loc
    r3.view_distance = dist
    r3.view_rotation = Euler(rot).to_quaternion()


class Overlay:
    """The operator's own drawing code on a stand-in for the running modal."""

    def __init__(self):
        cls = sd.VIEW3D_OT_shape_draw
        Fake = type("Fake", (), {k: v for k, v in cls.__dict__.items()
                                  if callable(v) or isinstance(v, (staticmethod, classmethod))})
        self.op = Fake()
        self.handles = []

    def show(self, **state):
        self.hide()
        s = self.op
        defaults = dict(pts=[], plane=None, hover=None, hover_plane=None, hover_face=None,
                        locked_face=None, hover_target=None, locked_target="", stage='DRAW',
                        lmb_down=False, press_px=None, typed="", len_typed="", cur_height=0.0,
                        anchor_xy=(0, 0), height_zero=0.0, closing=False, attach='OFF', axis=None,
                        mode='OUTLINE', q_anchor=None, q_end=None)
        for k, v in {**defaults, **state}.items():
            setattr(s, k, v)
        if s.plane is not None and s.hover_plane is None:
            s.hover_plane = s.plane
        add = bpy.types.SpaceView3D.draw_handler_add
        self.handles = [add(lambda: s._draw_3d_impl(bpy.context), (), 'WINDOW', 'POST_VIEW'),
                        add(lambda: s._draw_2d(bpy.context), (), 'WINDOW', 'POST_PIXEL')]

    def hide(self):
        for h in self.handles:
            bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        self.handles = []


OVERLAY = Overlay()


def crop_save(src, dst, aspect, width, fmt='PNG'):
    img = bpy.data.images.load(src)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)
    th = int(round(w / aspect))
    if th <= h:
        px, ch, cw = px[h - th:], th, w  # keep the top (rows run bottom-up), where the toolbar starts
    else:
        tw = int(round(h * aspect))
        px, ch, cw = px[:, :tw], h, tw  # keep the left edge, where the toolbar is
    out = bpy.data.images.new("crop", cw, ch, alpha=False)
    out.pixels.foreach_set(np.ascontiguousarray(px).ravel())
    out.scale(width, int(round(width / aspect)))
    out.filepath_raw = dst
    out.file_format = fmt
    out.save()
    bpy.data.images.remove(out)
    bpy.data.images.remove(img)
    print("wrote", dst)


# -- shots -----------------------------------------------------------------

def shot_draw():
    clear_scene()
    OVERLAY.show(pts=[dict(p) for p in ARCH[:4]], plane=GROUND, hover=(0.0, 1.5))
    view((1.6, 1.3, 0), 7.0, (0.9, 0, 0.45))


def shot_on_face():
    clear_scene()
    box("Block", (4, 3, 2), (1.5, 0.0, 1.0))
    front = (Vector((0, -1.5, 0)), Vector((0, -1, 0)), Vector((1, 0, 0)))  # local y is world up
    face = [Vector(v) for v in ((-0.5, -1.5, 0), (3.5, -1.5, 0), (3.5, -1.5, 2), (-0.5, -1.5, 2))]
    pts = [corner(1.0, 0.25), corner(2.0, 0.25), corner(2.0, 1.0),
           smooth(1.5, 1.5, (1.5 + 0.5 * K, 1.5), (1.5 - 0.5 * K, 1.5))]
    OVERLAY.show(pts=pts, plane=front, locked_face=face, hover=(1.0, 1.0),
                 locked_target="Block")
    view((1.5, -1.5, 1.0), 6.5, (1.25, 0, 0.35))


def shot_extrude():
    clear_scene()
    samples = sd.sample_outline(ARCH, 12)
    OVERLAY.show(pts=[dict(p) for p in ARCH], plane=GROUND, stage='EXTRUDE', cur_height=1.25,
                 anchor_xy=sd.bounds_center([s[0] for s in samples]))
    view((1.5, 1.3, 0.6), 7.5, (1.05, 0, 0.5))


def shot_typed():
    clear_scene()
    OVERLAY.show(pts=[corner(0, 0), corner(4, 0), corner(4, 1.5)], plane=GROUND,
                 hover=(1.5, 1.5), len_typed="2.5", axis='X')
    view((2.0, 0.8, 0), 7.0, (0.85, 0, 0.3))


def shot_results():
    OVERLAY.hide()
    clear_scene()
    base = build([corner(-1, -1), corner(6.5, -1), corner(6.5, 3.5), corner(-1, 3.5)], 0.4,
                 bevel=0.04, free_only=False)
    base.name = "Base"
    arch = build(ARCH, 1.8, origin=(0, 0, 0.4), bevel=0.04, attach='PARENT', target="Base")
    window = [corner(1.0, 0.6), corner(2.0, 0.6), corner(2.0, 1.2),
              smooth(1.5, 1.7, (1.5 + 0.5 * K, 1.7), (1.5 - 0.5 * K, 1.7)), corner(1.0, 1.2)]
    build(window, -0.5, origin=(0, 0, 0.4), normal=(0, -1, 0), attach='CUT', target=arch.name)
    build(circle(4.8, 1.2, 0.75), 1.0, origin=(0, 0, 0.4), bevel=0.06, attach='JOIN', target="Base")
    slot = [corner(4.3, -0.6), corner(5.9, -0.6), corner(5.9, -0.3), corner(4.3, -0.3)]
    build(slot, -0.2, origin=(0, 0, 0.4), attach='CUT', target="Base")
    for ob in bpy.context.view_layer.objects:
        ob.select_set(False)
    view((2.6, 1.2, 0.6), 10.0, (1.1, 0, 0.62))


def shot_quick():
    clear_scene()
    box("Block", (4, 3, 1), (1.5, 0.5, 0.5))
    top = (Vector((0, 0, 1)), Vector((0, 0, 1)), Vector((1, 0, 0)))
    face = [Vector(v) for v in ((-0.5, -1, 1), (3.5, -1, 1), (3.5, 2, 1), (-0.5, 2, 1))]
    OVERLAY.show(mode='CIRCLE', plane=top, locked_face=face, locked_target="Block",
                 q_anchor=(1.5, 0.5), q_end=(2.5, 0.5), pts=sd.circle_points((1.5, 0.5), 1.0))
    view((1.5, 0.5, 0.6), 7.0, (0.95, 0, 0.4))


SHOT_LIST = [("01-draw", shot_draw), ("02-on-a-face", shot_on_face), ("03-extrude", shot_extrude),
             ("04-typed-length", shot_typed), ("05-results", shot_results),
             ("06-quick-circle", shot_quick)]


def setup_ui():
    win, area, region = ui()
    space = area.spaces.active
    space.shading.type = 'SOLID'
    space.shading.light = 'STUDIO'
    space.shading.show_cavity = True
    space.shading.show_shadows = True
    space.shading.color_type = 'MATERIAL'
    space.overlay.show_stats = False
    space.show_region_ui = False
    space.show_gizmo_navigate = False
    space.overlay.show_text = False
    space.overlay.show_cursor = False
    space.overlay.show_object_origins = False
    space.show_region_header = False       # no headers: nothing but the toolbar and the viewport
    space.show_region_tool_header = False
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.screen_full_area(use_hide_panels=False)
        bpy.ops.wm.tool_set_by_id(name="shape_draw.tool")


def run():
    steps = []

    def first():
        setup_ui()
        return 1.0

    steps.append(first)
    for name, stage in SHOT_LIST:
        def do_stage(stage=stage):
            stage()
            for a in ui()[0].screen.areas:
                a.tag_redraw()
            return 1.0

        def do_capture(name=name):
            win, area, _region = ui()
            raw = os.path.join(SHOTS, f"_{name}_raw.png")
            with bpy.context.temp_override(window=win, area=area):
                bpy.ops.screen.screenshot_area(filepath=raw)
            crop_save(raw, os.path.join(SHOTS, f"{name}.png"), 2.0, 1920)
            # 16:9 copies for extensions.blender.org, which shows previews at that ratio
            crop_save(raw, os.path.join(PREVIEWS, f"{name}.png"), 16 / 9, 1920)
            if name == "05-results":  # featured images come from the finished model
                crop_save(raw, os.path.join(HERE, "featured.png"), 16 / 9, 1920)
                crop_save(raw, os.path.join(HERE, "featured.jpg"), 2.0, 1920, 'JPEG')
            os.remove(raw)
            return 0.2

        steps += [do_stage, do_capture]

    def done():
        OVERLAY.hide()
        print("SCREENSHOTS_OK")
        bpy.ops.wm.quit_blender()
        return None

    steps.append(done)

    def tick():
        try:
            return steps.pop(0)()
        except Exception:
            import traceback
            traceback.print_exc()
            bpy.ops.wm.quit_blender()
            return None

    bpy.app.timers.register(tick, first_interval=1.5)


if __name__ == "__main__":
    run()
