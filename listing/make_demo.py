"""README demo: an animated GIF of a shape being drawn, curved, pulled up, bevelled and cut.

    python listing/make_demo.py

Run with plain Python (needs Pillow). It starts Blender in its own window to capture the frames
(the same staging as make_screenshots.py: the add-on's own overlay on a stand-in for the running
tool, plus a drawn mouse cursor), then packs them into listing/demo.gif with one shared palette so
each frame only stores what changed. Your own Blender session and files aren't touched.
"""
import math
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMES = os.path.join(HERE, "_frames")
OUT = os.path.join(HERE, "demo.gif")
WIDTH = 960          # GIF width (2:1, so 960 x 480)
FPS = 20

try:
    import bpy  # noqa: F401
    IN_BLENDER = True
except ImportError:
    IN_BLENDER = False


# ---------------------------------------------------------------------------
# Inside Blender: stage and capture the frames
# ---------------------------------------------------------------------------

def capture():
    import bpy
    import gpu
    from bpy_extras import view3d_utils
    from gpu_extras.batch import batch_for_shader
    from mathutils import Vector

    sys.path.insert(0, HERE)
    import make_screenshots as ms
    sd = ms.sd
    corner, smooth = ms.corner, ms.smooth

    # A bracket: square body, a round knob on the right, a scooped curve on the left.
    OUTLINE = [
        corner(0, 0), corner(3, 0), corner(3, 1),
        smooth(4.25, 2, (4.25, 1.3), (4.25, 2.7)),
        corner(3, 3), corner(3, 4), corner(0, 4), corner(0, 3),
        smooth(1, 2, (1, 2.6), (1, 1.4)),
        corner(0, 1),
    ]
    HOLE = ms.circle(2.0, 2.0, 0.6)
    HEIGHT, BEVEL, CUT = 1.0, 0.08, -0.5
    BEVEL_START = 0.01  # a near-zero bevel shades the cap badly, so start small but real
    VIEW_AT, VIEW_DIST = (2.1, 1.6, 0.4), 7.6
    TOP = (Vector((0, 0, HEIGHT)), Vector((0, 0, 1)), Vector((1, 0, 0)))

    frames = []          # callables that set up one frame
    holds = {}           # frame index -> extra frames to hold it for
    cursor = {"xy": None, "plane": ms.GROUND, "down": False}
    op = ms.OVERLAY.op

    # -- the drawn mouse cursor (the real one isn't in screenshots) ----------
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    arrow = [(0, 0), (0, -21), (5, -16), (9, -25), (12, -24), (8, -15), (15, -15)]

    def draw_cursor():
        if cursor["xy"] is None:
            return
        ctx = bpy.context
        o, n, x = cursor["plane"]
        y = n.cross(x)
        w = o + x * cursor["xy"][0] + y * cursor["xy"][1]
        p = view3d_utils.location_3d_to_region_2d(ctx.region, ctx.region_data, w)
        if p is None:
            return
        pts = [(p.x + a, p.y + b) for a, b in arrow]
        tris = [(0, 1, 2), (0, 2, 6), (2, 3, 4), (2, 4, 5), (2, 5, 6)]
        gpu.state.blend_set('ALPHA')
        for scale, col in ((1.12, (0, 0, 0, 1)), (1.0, (1, 1, 1, 1))):  # black outline, white fill
            q = [(p.x + (a - p.x) * scale, p.y + (b - p.y) * scale) for a, b in pts]
            shader.bind()
            shader.uniform_float("color", col)
            batch_for_shader(shader, 'TRIS', {"pos": [q[i] for t in tris for i in t]}).draw(shader)
        if cursor["down"]:  # click ring
            ring = [(p.x + 9 * math.cos(a * math.pi / 8), p.y + 9 * math.sin(a * math.pi / 8)) for a in range(17)]
            shader.bind()
            shader.uniform_float("color", (1, 1, 1, 0.8))
            batch_for_shader(shader, 'LINE_STRIP', {"pos": ring}).draw(shader)
        gpu.state.blend_set('NONE')

    # -- frame builders ------------------------------------------------------
    def ease(t):
        return t * t * (3 - 2 * t)

    def lerp(a, b, t):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    def state(**kw):
        for k, v in kw.items():
            setattr(op, k, v)

    def add(fn, hold=0):
        frames.append(fn)
        if hold:
            holds[len(frames) - 1] = hold

    def draw_outline(points, plane, face=None, start_from=None, glide=5, drag=5):
        """Cursor glides to each point, clicks it, drags the handles of smooth ones."""
        placed = []
        prev = start_from or (points[0]["co"][0] - 1.5, points[0]["co"][1] - 1.0)
        for p in points:
            target = p["co"]
            for f in range(1, glide + 1):
                def fr(t=ease(f / glide), a=prev, b=target, done=list(placed)):
                    xy = lerp(a, b, t)
                    cursor.update(xy=xy, plane=plane, down=False)
                    state(pts=[dict(q) for q in done], hover=xy, lmb_down=False, closing=False)
                add(fr)
            placed.append(dict(p, h_in=p["co"], h_out=p["co"], smooth=False))
            if p["smooth"]:
                for f in range(1, drag + 1):
                    def fr(t=ease(f / drag), p=p, done=[dict(q) for q in placed]):
                        hout = lerp(p["co"], p["h_out"], t)
                        hin = lerp(p["co"], p["h_in"], t)
                        done[-1].update(h_in=hin, h_out=hout, smooth=True)
                        cursor.update(xy=hout, plane=plane, down=True)
                        state(pts=done, hover=None, lmb_down=True, closing=False)
                    add(fr)
                placed[-1] = dict(p)
                prev = p["h_out"]
            else:
                def fr(xy=target, done=[dict(q) for q in placed]):
                    cursor.update(xy=xy, plane=plane, down=True)
                    state(pts=done, hover=xy, lmb_down=False, closing=False)
                add(fr)
                prev = target
        # back to the first point to close
        first = points[0]["co"]
        for f in range(1, glide + 1):
            def fr(t=ease(f / glide), a=prev, done=[dict(q) for q in placed]):
                xy = lerp(a, first, t)
                cursor.update(xy=xy, plane=plane, down=False)
                state(pts=done, hover=xy, lmb_down=False, closing=t > 0.8)
            add(fr)
        return placed

    def extrude(points, plane, height, frames_n=14, hold=8):
        samples = sd.sample_outline(points, 12)
        anchor = sd.bounds_center([s[0] for s in samples])
        for f in range(1, frames_n + 1):
            def fr(t=ease(f / frames_n)):
                cursor.update(xy=(anchor[0] + 1.6, anchor[1] - 1.2), plane=plane, down=False)
                state(stage='EXTRUDE', pts=[dict(q) for q in points], anchor_xy=anchor,
                      cur_height=round(height * t / 0.05) * 0.05, hover=None)
            add(fr, hold if f == frames_n else 0)

    # Phase 1: draw the bracket on the ground, then pull it up.
    def start_phase1():
        ms.clear_scene()
        ms.OVERLAY.show(plane=ms.GROUND)
        ms.view(VIEW_AT, VIEW_DIST, (0.92, 0, 0.55))
    add(start_phase1)
    pts = draw_outline(OUTLINE, ms.GROUND)
    extrude(pts, ms.GROUND, HEIGHT)

    # Phase 2: the real mesh, bevel growing.
    shape_name = {}

    def make_shape():
        ms.OVERLAY.hide()
        cursor["xy"] = None
        ob = ms.build(OUTLINE, HEIGHT, bevel=BEVEL_START, free_only=False)
        shape_name["n"] = ob.name
        ob.select_set(False)
    add(make_shape, 4)
    for f in range(1, 11):
        def fr(t=ease(f / 10)):
            ob = bpy.data.objects[shape_name["n"]]
            mod = next(m for m in ob.modifiers if m.type == 'BEVEL')
            mod.width = BEVEL_START + (BEVEL - BEVEL_START) * t
            mod.segments = 3
        add(fr, 6 if f == 10 else 0)

    # Phase 3: a round hole drawn on the top face and cut in.
    top_face = [Vector((x, y, HEIGHT)) for (x, y), _c, _s in sd.sample_outline(OUTLINE, 16)]

    def start_phase3():
        ms.OVERLAY.show(plane=TOP, locked_face=top_face, locked_target=shape_name["n"], attach='CUT')
    add(start_phase3)
    hole = draw_outline(HOLE, TOP, start_from=(3.2, 0.6), glide=4, drag=4)
    extrude(hole, TOP, CUT, frames_n=8, hold=4)

    def make_cut():
        ms.OVERLAY.hide()
        cursor["xy"] = None
        ms.build(HOLE, CUT, origin=(0, 0, HEIGHT), attach='CUT', target=shape_name["n"])
        for ob in bpy.context.view_layer.objects:
            ob.select_set(False)
    add(make_cut, 2)

    # Phase 4: a slow turn to show it off.
    for f in range(1, 25):
        def fr(t=ease(f / 24)):
            ms.view(VIEW_AT, VIEW_DIST - 0.8 * t, (0.92 - 0.12 * t, 0, 0.55 + 0.7 * t))
        add(fr, 14 if f == 24 else 0)

    # -- capture loop ----------------------------------------------------------
    if os.path.isdir(FRAMES):
        shutil.rmtree(FRAMES)
    os.makedirs(FRAMES)
    bpy.context.preferences.view.show_splash = False
    steps = {"i": -1, "phase": "setup"}

    def tick():
        try:
            i = steps["i"]
            if steps["phase"] == "setup":  # once the window is up (this also closes the splash)
                ms.setup_ui()
                # Workbench's stencil shadows streak across big concave caps (off by default anyway)
                ms.ui()[1].spaces.active.shading.show_shadows = False
                bpy.types.SpaceView3D.draw_handler_add(draw_cursor, (), 'WINDOW', 'POST_PIXEL')
                steps["phase"] = "apply"
                return 1.0
            if steps["phase"] == "apply":
                i += 1
                steps["i"] = i
                if i >= len(frames):
                    with open(os.path.join(FRAMES, "holds.txt"), "w") as fh:
                        fh.write(repr(holds))
                    print("FRAMES_OK", len(frames))
                    bpy.ops.wm.quit_blender()
                    return None
                frames[i]()
                for a in ms.ui()[0].screen.areas:
                    a.tag_redraw()
                steps["phase"] = "capture"
                return 0.05
            win, area, _region = ms.ui()
            with bpy.context.temp_override(window=win, area=area):
                bpy.ops.screen.screenshot_area(filepath=os.path.join(FRAMES, f"{i:04d}.png"))
            steps["phase"] = "apply"
            return 0.01
        except Exception:
            import traceback
            traceback.print_exc()
            bpy.ops.wm.quit_blender()
            return None

    bpy.app.timers.register(tick, first_interval=1.5)


# ---------------------------------------------------------------------------
# Plain Python: run Blender, then build the GIF
# ---------------------------------------------------------------------------

def assemble():
    from PIL import Image

    names = sorted(f for f in os.listdir(FRAMES) if f.endswith(".png"))
    holds = eval(open(os.path.join(FRAMES, "holds.txt")).read())  # our own repr of a dict
    imgs = []
    for name in names:
        im = Image.open(os.path.join(FRAMES, name)).convert("RGB")
        w, h = im.size
        th = min(h, w // 2)
        im = im.crop((0, 0, th * 2, th))  # 2:1 from the top-left, keeping the toolbar whole
        imgs.append(im.resize((WIDTH, WIDTH // 2), Image.LANCZOS))
    # One palette for every frame (built from a strip of frames), so unchanged pixels stay
    # identical and Pillow only stores the changed region of each frame.
    sample = Image.new("RGB", (WIDTH, (WIDTH // 2) * 4))
    for k, idx in enumerate((len(imgs) // 5, len(imgs) // 2, int(len(imgs) * 0.7), len(imgs) - 1)):
        sample.paste(imgs[idx], (0, k * (WIDTH // 2)))
    pal = sample.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    frames = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in imgs]
    base = 1000 // FPS
    durations = [base * (1 + holds.get(i, 0)) for i in range(len(frames))]
    frames[0].save(OUT, save_all=True, append_images=frames[1:], duration=durations,
                   loop=0, optimize=False, disposal=1)
    print(f"wrote {OUT}: {len(frames)} frames, {sum(durations) / 1000:.1f} s, "
          f"{os.path.getsize(OUT) / 1e6:.2f} MB")
    shutil.rmtree(FRAMES)


def main():
    sys.path.insert(0, os.path.dirname(HERE))
    from make import find_blender
    res = subprocess.run([find_blender(), "--factory-startup", "--window-geometry", "0", "0", "1920", "1080",
                          "--python", os.path.abspath(__file__)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    out = res.stdout + res.stderr
    if "FRAMES_OK" not in out:
        print(out[-3000:])
        sys.exit("Blender didn't finish capturing the frames")
    assemble()


if IN_BLENDER:
    capture()
elif __name__ == "__main__":
    main()
