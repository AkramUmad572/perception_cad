"""CadQuery (preferred) + trimesh fallback builders for ring/box/cylinder."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)

DEFAULTS: dict[str, dict[str, Any]] = {
    "ring": {
        "inner_diameter_mm": 18.0,
        "outer_diameter_mm": 22.0,
        "height_mm": 4.0,
        "color": "#C0C0C0",
    },
    "box": {
        "width_mm": 40.0,
        "depth_mm": 40.0,
        "height_mm": 20.0,
        "color": "#C0C0C0",
    },
    "cylinder": {
        "diameter_mm": 30.0,
        "height_mm": 40.0,
        "color": "#C0C0C0",
    },
}


def merge_params(template: str, params: dict[str, Any]) -> dict[str, Any]:
    base = dict(DEFAULTS[template])
    for key, value in params.items():
        if value is not None:
            base[key] = value
    return base


def _hex_to_rgba(color: str) -> tuple[float, float, float, float]:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    r = int(c[0:2], 16) / 255.0
    g = int(c[2:4], 16) / 255.0
    b = int(c[4:6], 16) / 255.0
    return r, g, b, 1.0


def _cadquery_available() -> bool:
    try:
        import cadquery  # noqa: F401

        return True
    except Exception:
        return False


def _build_with_cadquery(template: str, params: dict[str, Any], out_glb: Path) -> None:
    import cadquery as cq
    from cadquery import exporters

    if template == "ring":
        outer_r = params["outer_diameter_mm"] / 2.0
        inner_r = params["inner_diameter_mm"] / 2.0
        height = params["height_mm"]
        if inner_r >= outer_r:
            raise ValueError("inner_diameter must be smaller than outer_diameter")
        solid = (
            cq.Workplane("XY")
            .circle(outer_r)
            .circle(inner_r)
            .extrude(height)
        )
    elif template == "box":
        solid = cq.Workplane("XY").box(
            params["width_mm"],
            params["depth_mm"],
            params["height_mm"],
        )
    elif template == "cylinder":
        solid = (
            cq.Workplane("XY")
            .circle(params["diameter_mm"] / 2.0)
            .extrude(params["height_mm"])
        )
    else:
        raise ValueError(f"Unknown template: {template}")

    # CadQuery GLTF export; convert path via temp if needed
    # exporters.export supports gltf in recent versions — use STL then trimesh for GLB reliability
    stl_path = out_glb.with_suffix(".stl")
    exporters.export(solid, str(stl_path))

    import trimesh

    mesh = trimesh.load_mesh(str(stl_path))
    if isinstance(mesh, trimesh.Scene):
        mesh = trimesh.util.concatenate(tuple(mesh.geometry.values()))
    rgba = _hex_to_rgba(params.get("color", "#C0C0C0"))
    mesh.visual.vertex_colors = [int(rgba[0] * 255), int(rgba[1] * 255), int(rgba[2] * 255), 255]
    mesh.export(str(out_glb), file_type="glb")
    stl_path.unlink(missing_ok=True)


def _build_with_trimesh(template: str, params: dict[str, Any], out_glb: Path) -> None:
    import numpy as np
    import trimesh

    rgba = _hex_to_rgba(params.get("color", "#C0C0C0"))
    color = [int(rgba[0] * 255), int(rgba[1] * 255), int(rgba[2] * 255), 255]

    if template == "ring":
        outer_r = params["outer_diameter_mm"] / 2.0
        inner_r = params["inner_diameter_mm"] / 2.0
        height = params["height_mm"]
        outer = trimesh.creation.cylinder(radius=outer_r, height=height, sections=64)
        inner = trimesh.creation.cylinder(radius=inner_r, height=height * 1.05, sections=64)
        mesh = outer.difference(inner)
    elif template == "box":
        mesh = trimesh.creation.box(
            extents=[params["width_mm"], params["depth_mm"], params["height_mm"]]
        )
    elif template == "cylinder":
        mesh = trimesh.creation.cylinder(
            radius=params["diameter_mm"] / 2.0,
            height=params["height_mm"],
            sections=64,
        )
    else:
        raise ValueError(f"Unknown template: {template}")

    if isinstance(mesh, trimesh.Scene):
        mesh = trimesh.util.concatenate(tuple(mesh.geometry.values()))
    mesh.visual.vertex_colors = np.tile(color, (len(mesh.vertices), 1))
    # Scale mm → meters for WebXR (1 unit ≈ 1m). Keep models ~hand-sized.
    mesh.apply_scale(0.001)
    mesh.export(str(out_glb), file_type="glb")


def build_model(
    template: str,
    params: dict[str, Any],
    settings: Settings,
) -> tuple[str, Path, float]:
    """Build template to GLB. Returns (model_id, path, build_ms)."""
    if template not in DEFAULTS:
        raise ValueError(f"Unknown template: {template}")

    merged = merge_params(template, params)
    model_id = uuid.uuid4().hex[:12]
    out_glb = settings.glb_dir / f"{model_id}.glb"

    t0 = time.perf_counter()
    use_cq = settings.prefer_cadquery and _cadquery_available()
    try:
        if use_cq:
            _build_with_cadquery(template, merged, out_glb)
            # CadQuery builds in mm; scale to meters for WebXR
            import trimesh

            mesh = trimesh.load(str(out_glb), force="mesh")
            mesh.apply_scale(0.001)
            rgba = _hex_to_rgba(merged.get("color", "#C0C0C0"))
            mesh.visual.vertex_colors = [
                int(rgba[0] * 255),
                int(rgba[1] * 255),
                int(rgba[2] * 255),
                255,
            ]
            mesh.export(str(out_glb), file_type="glb")
            engine = "cadquery"
        else:
            _build_with_trimesh(template, merged, out_glb)
            engine = "trimesh"
    except Exception as exc:
        logger.warning("CadQuery build failed (%s); falling back to trimesh", exc)
        _build_with_trimesh(template, merged, out_glb)
        engine = "trimesh-fallback"

    build_ms = (time.perf_counter() - t0) * 1000
    logger.info("Built %s via %s in %.1fms → %s", template, engine, build_ms, out_glb.name)
    return model_id, out_glb, build_ms
