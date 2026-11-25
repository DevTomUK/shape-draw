"""Blender extension entry point. All real code lives in shape_draw.py."""
from .shape_draw import register, unregister

__all__ = ["register", "unregister"]
