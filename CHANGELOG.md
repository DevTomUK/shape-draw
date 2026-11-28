# Changelog

## 0.2.0

- Edit Outline: reopen a shape's outline to move points, reshape curves, add or remove points,
  switch points between corner and curve, and change the height. The object is rebuilt in place,
  keeping its position, parent, modifiers and materials. E with the tool active, the tool header,
  or the Object and right-click menus. With a cut target selected, it edits the newest cutter.
- Listing tags are now Modeling and Add Mesh.

## 0.1.0

First release.

- Draw Shape tool in the 3D View toolbar, and Add > Draw Shape
- Click for sharp corners, drag for smooth curved points
- Quick rectangle (Shift for a square) and circle (typed radius) shapes: P / R / C or the tool header
- Draw on the ground or on any face, with the face highlighted and a snap grid shown on it
- Live edge lengths while drawing, in the scene's units
- Type a number for an exact edge length; Shift locks to 15 degree steps
- Edges lock onto the grid's X / Y axis when nearly lined up, even with snapping off
- Pull the outline up into a solid with the mouse, or type an exact height
- Redo panel: height, mesh or curve output, bevel (with Bevel Segments and an Only Free Edges option), Curve Segments for curved outlines
- Attach to the clicked object: parent to it, join into it, or cut into it with a live boolean
