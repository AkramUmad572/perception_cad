"""
CadQuery model builder with sandboxed freeform execution.

PRIMARY PATH (free-rein):
- ai.intent generates CadQuery Python for ANY shape user describes
- pipeline executes via execute_cadquery (sandbox) with retry loop
- No whitelist restrictions on shapes

LEGACY PATH (backward compat only):
- Template-based builds (ring/box/cylinder) in build_model()
- Used only when action="create" with known template name
"""

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
    """Merge user params with template defaults."""
    if template not in DEFAULTS:
        return dict(params)
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


def _generate_template_script(template: str, params: dict[str, Any]) -> str:
    """Generate CadQuery script from template name and params."""
    if template == "ring":
        outer_r = params["outer_diameter_mm"] / 2.0
        inner_r = params["inner_diameter_mm"] / 2.0
        height = params["height_mm"]
        return f"""
import cadquery as cq
result = (
    cq.Workplane("XY")
    .circle({outer_r})
    .circle({inner_r})
    .extrude({height})
)
"""
    elif template == "box":
        return f"""
import cadquery as cq
result = cq.Workplane("XY").box(
    {params["width_mm"]},
    {params["depth_mm"]},
    {params["height_mm"]},
)
"""
    elif template == "cylinder":
        return f"""
import cadquery as cq
result = (
    cq.Workplane("XY")
    .circle({params["diameter_mm"] / 2.0})
    .extrude({params["height_mm"]})
)
"""
    else:
        raise ValueError(f"Unknown template: {template}")


def _build_with_cadquery(template: str, params: dict[str, Any], out_glb: Path) -> None:
    """Build using direct CadQuery (legacy path)."""
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
    """Fallback builder using trimesh primitives."""
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
    mesh.apply_scale(0.001)
    mesh.export(str(out_glb), file_type="glb")


def build_model(
    template: str,
    params: dict[str, Any],
    settings: Settings,
) -> tuple[str, Path, float]:
    """
    Build template to GLB. Returns (model_id, path, build_ms).

    This is the legacy template-based builder for backward compatibility.
    For freeform CadQuery, use build_from_script().
    """
    if template not in DEFAULTS:
        raise ValueError(f"Unknown template: {template}. Use build_from_script() for freeform.")

    merged = merge_params(template, params)
    model_id = uuid.uuid4().hex[:12]
    out_glb = settings.glb_dir / f"{model_id}.glb"

    t0 = time.perf_counter()
    use_cq = settings.prefer_cadquery and _cadquery_available()
    try:
        if use_cq:
            _build_with_cadquery(template, merged, out_glb)
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


def build_from_script(
    script: str,
    settings: Settings,
    color: str = "#C0C0C0",
    timeout: float = 30.0,
) -> tuple[str, Path, float]:
    """
    Build GLB from freeform CadQuery script using sandboxed execution.

    Args:
        script: CadQuery Python script (must define 'result', 'solid', or 'model')
        settings: App settings with glb_dir
        color: Hex color to apply
        timeout: Max execution time in seconds

    Returns:
        Tuple of (model_id, glb_path, build_ms)

    Raises:
        SandboxError, TimeoutError, NonManifoldError, SecurityError
    """
    from cad.sandbox import execute_cadquery_script, SandboxError

    result = execute_cadquery_script(
        script=script,
        output_dir=settings.glb_dir,
        timeout=timeout,
        color=color,
    )

    if not result["ok"]:
        raise SandboxError(result["error"])

    return result["model_id"], Path(result["glb_path"]), result["exec_ms"]


def build_any(
    template: str | None,
    script: str | None,
    params: dict[str, Any],
    settings: Settings,
    color: str = "#C0C0C0",
) -> tuple[str, Path, float]:
    """
    Unified builder: template-based or freeform script.

    If script is provided, uses sandboxed freeform execution.
    Otherwise falls back to template-based building.
    """
    if script:
        return build_from_script(script, settings, color=color)

    if template and template in DEFAULTS:
        merged = merge_params(template, params)
        merged["color"] = color
        return build_model(template, merged, settings)

    raise ValueError("Either 'script' or valid 'template' must be provided")
