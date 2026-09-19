"""
Sandboxed CadQuery script execution.

Public API:
    execute_cadquery_script(script: str) -> { ok, glb_path | error }

Provides safe execution of generated CadQuery scripts with:
- Hard timeout (kills hung exec)
- No filesystem access (blocked builtins)
- No network access (no socket/urllib)
- Non-manifold repair (still exports a viewable mesh)
- Memory limits via resource module
"""

from __future__ import annotations

import logging
import multiprocessing
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EXEC_TIMEOUT_SEC = 45
MAX_VERTICES = 500_000
MAX_FACES = 500_000

BLOCKED_BUILTINS = frozenset([
    "open",
    "exec",
    "eval",
    "compile",
    "__import__",
    "input",
    "breakpoint",
    "getattr",
    "setattr",
    "delattr",
    "type",
    "object",
    "vars",
    "dir",
    "globals",
    "locals",
    "memoryview",
    "classmethod",
    "staticmethod",
    "property",
    "super",
])

BLOCKED_MODULES = frozenset([
    "os",
    "sys",
    "subprocess",
    "socket",
    "urllib",
    "requests",
    "http",
    "ftplib",
    "smtplib",
    "pathlib",
    "shutil",
    "glob",
    "pickle",
    "marshal",
    "ctypes",
    "multiprocessing",
    "threading",
])

ALLOWED_MODULES = frozenset([
    "math",
    "decimal",
    "fractions",
    "cmath",
    "itertools",
    "functools",
    "operator",
    "collections",
])

ALLOWED_CADQUERY_ATTRS = frozenset([
    # Core geometry classes
    "Workplane",
    "CQ",  # Alias for Workplane used in some scripts
    "Assembly",
    "Sketch",
    # Primitive types
    "Vector",
    "Location",
    "Plane",
    "Matrix",
    "BoundBox",
    # Shape types
    "Solid",
    "Shell",
    "Face",
    "Wire",
    "Edge",
    "Vertex",
    "Shape",
    "Compound",
    # Utilities
    "Color",
    # Selection helpers (used for .faces(">Z") style selectors)
    "selectors",
    "StringSyntaxSelector",
    "DirectionSelector",
    "DirectionMinMaxSelector",
    "NearestToPointSelector",
    "TypeSelector",
    "ParallelDirSelector",
    "PerpendicularDirSelector",
])


class SandboxError(Exception):
    """Raised when sandbox execution fails."""
    pass


class TimeoutError(SandboxError):
    """Raised when execution times out."""
    pass


class NonManifoldError(SandboxError):
    """Raised when mesh is non-manifold."""
    pass


class SecurityError(SandboxError):
    """Raised when script attempts blocked operations."""
    pass


def _make_hex_color(real_color):
    """
    cq.Color that also takes "#RRGGBB".

    CadQuery only accepts named colours or RGB floats, but hex is what the
    codegen prompt asks for and what a photo's palette comes back as.
    """

    def Color(*args, **kwargs):
        if len(args) == 1 and isinstance(args[0], str) and args[0].startswith("#"):
            r, g, b = _hex_to_rgb(args[0])
            return real_color(r / 255.0, g / 255.0, b / 255.0, 1.0)
        return real_color(*args, **kwargs)

    return Color


class _SafeCadQueryProxy:
    """
    Thin proxy exposing only allowlisted CadQuery classes.
    
    Blocks access to full cadquery module internals that may carry
    filesystem/network parent references via __module__, __file__, etc.
    """
    __slots__ = ("_allowed",)

    def __init__(self):
        import cadquery as _real_cq
        self._allowed = {}
        for attr in ALLOWED_CADQUERY_ATTRS:
            if hasattr(_real_cq, attr):
                self._allowed[attr] = getattr(_real_cq, attr)
        if "Color" in self._allowed:
            self._allowed["Color"] = _make_hex_color(self._allowed["Color"])

    def __getattr__(self, name: str):
        if name in self._allowed:
            return self._allowed[name]
        raise SecurityError(
            f"Access to 'cq.{name}' is not allowed in sandbox "
            f"(allowed: {', '.join(sorted(ALLOWED_CADQUERY_ATTRS))})"
        )

    def __dir__(self):
        return list(ALLOWED_CADQUERY_ATTRS)


def _create_safe_builtins():
    """Create restricted builtins dict."""
    import builtins
    safe = {}
    for name in dir(builtins):
        if name not in BLOCKED_BUILTINS and not name.startswith("_"):
            safe[name] = getattr(builtins, name)
    safe["__builtins__"] = safe
    return safe


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Restricted import that ONLY allows explicitly allowlisted modules."""
    base_module = name.split(".")[0]

    if base_module not in ALLOWED_IMPORT_MODULES:
        raise SecurityError(f"Import of '{name}' is not allowed in sandbox (allowlist: {', '.join(sorted(ALLOWED_IMPORT_MODULES))})")

    # Return the safe proxy for cadquery imports instead of the real module
    if base_module in ("cadquery", "cq"):
        return _SafeCadQueryProxy()

    import importlib
    return importlib.import_module(name)


ALLOWED_IMPORT_MODULES = ALLOWED_MODULES | frozenset(["cadquery", "cq"])


def _validate_script(script: str) -> None:
    """Static validation of script before execution."""
    import ast

    try:
        tree = ast.parse(script)
    except SyntaxError as e:
        raise SandboxError(f"Syntax error in script: {e}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                base = alias.name.split(".")[0]
                if base not in ALLOWED_IMPORT_MODULES:
                    raise SecurityError(
                        f"Import of '{alias.name}' is not allowed "
                        f"(allowed: {', '.join(sorted(ALLOWED_IMPORT_MODULES))})"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                base = node.module.split(".")[0]
                if base not in ALLOWED_IMPORT_MODULES:
                    raise SecurityError(
                        f"Import from '{node.module}' is not allowed "
                        f"(allowed: {', '.join(sorted(ALLOWED_IMPORT_MODULES))})"
                    )


def _check_manifold(mesh) -> bool:
    """Check if trimesh is manifold (watertight)."""
    if hasattr(mesh, "is_watertight"):
        return mesh.is_watertight
    if hasattr(mesh, "is_volume"):
        return mesh.is_volume
    return True


def _check_mesh_limits(mesh) -> None:
    """Check mesh doesn't exceed vertex/face limits."""
    if hasattr(mesh, "vertices") and len(mesh.vertices) > MAX_VERTICES:
        raise SandboxError(f"Mesh exceeds vertex limit: {len(mesh.vertices)} > {MAX_VERTICES}")
    if hasattr(mesh, "faces") and len(mesh.faces) > MAX_FACES:
        raise SandboxError(f"Mesh exceeds face limit: {len(mesh.faces)} > {MAX_FACES}")


def _is_assembly(obj) -> bool:
    if obj is None:
        return False
    if type(obj).__name__ == "Assembly":
        return True
    return (
        hasattr(obj, "children")
        and hasattr(obj, "objects")
        and hasattr(obj, "add")
    )


def _iter_assembly_nodes(node, parent_loc=None) -> list:
    """(node, world location) per solid, with parent transforms composed in."""
    loc = getattr(node, "loc", None)
    if parent_loc is None:
        world = loc
    elif loc is None:
        world = parent_loc
    else:
        world = parent_loc * loc

    nodes = []
    if getattr(node, "obj", None) is not None:
        nodes.append((node, world))
    for child in getattr(node, "children", []) or []:
        nodes.extend(_iter_assembly_nodes(child, world))
    return nodes


def _node_rgba(node, fallback: tuple[int, int, int, int] = (192, 192, 192, 255)):
    color = getattr(node, "color", None)
    if color is None:
        return fallback
    try:
        t = color.toTuple()
        a = t[3] if len(t) > 3 else 1.0
        return (int(t[0] * 255), int(t[1] * 255), int(t[2] * 255), int(a * 255))
    except Exception:
        return fallback


def _shape_for_export(node, world_loc=None):
    obj = node.obj
    shape = obj.val() if hasattr(obj, "val") else obj
    # `located` REPLACES a shape's transform, so an identity assembly location
    # dragged every part back to the origin. `moved` composes with what the
    # script already baked in via .transformed(offset=...).
    if world_loc is not None and hasattr(shape, "moved"):
        try:
            shape = shape.moved(world_loc)
        except Exception:
            pass
    return shape


def _prepare_trimesh(mesh):
    import trimesh

    if isinstance(mesh, trimesh.Scene):
        mesh = trimesh.util.concatenate(tuple(mesh.geometry.values()))
    _check_mesh_limits(mesh)
    for step_name in ("fix_winding", "fix_normals"):
        try:
            getattr(trimesh.repair, step_name)(mesh)
        except Exception as e:
            logger.warning("Mesh %s failed: %s", step_name, e)
    if not _check_manifold(mesh):
        logger.warning("Non-manifold mesh detected, attempting repair")
        try:
            trimesh.repair.fill_holes(mesh)
        except Exception as e:
            logger.warning("Mesh fill_holes failed: %s", e)
        if not _check_manifold(mesh):
            faces = getattr(mesh, "faces", None)
            try:
                n_faces = 0 if faces is None else int(len(faces))
            except TypeError:
                n_faces = 0
            if n_faces < 4:
                raise NonManifoldError("Generated mesh is empty or degenerate")
            logger.warning("Exporting non-watertight mesh (%d faces) for XR", n_faces)
    mesh.apply_scale(0.001)
    return mesh


def _export_solid(shape, stl_path: str):
    from cadquery import exporters
    exporters.export(shape, stl_path)
    import trimesh
    mesh = trimesh.load_mesh(stl_path)
    return _prepare_trimesh(mesh)


def _export_assembly(assy, out_glb: str) -> bool:
    import trimesh

    nodes = _iter_assembly_nodes(assy)
    if not nodes:
        raise SandboxError("Assembly has no solid parts")

    scene = trimesh.Scene()
    used_names: set[str] = set()
    for i, (node, world_loc) in enumerate(nodes):
        stl_path = out_glb.replace(".glb", f"_p{i}.stl")
        try:
            shape = _shape_for_export(node, world_loc)
            mesh = _export_solid(shape, stl_path)
            r, g, b, a = _node_rgba(node)
            mesh.visual.vertex_colors = [r, g, b, a]
            name = str(getattr(node, "name", None) or f"part_{i}")
            base = name
            n = 2
            while name in used_names:
                name = f"{base}_{n}"
                n += 1
            used_names.add(name)
            scene.add_geometry(mesh, geom_name=name, node_name=name)
        finally:
            try:
                Path(stl_path).unlink(missing_ok=True)
            except Exception:
                pass

    if not scene.geometry:
        raise SandboxError("Assembly exported no geometry")
    scene.export(out_glb)
    return True


def _run_in_sandbox(script: str, out_glb: str, result_queue: multiprocessing.Queue):
    """Execute script in sandboxed subprocess."""
    try:
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 * 1024 * 1024, 2 * 1024 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_CPU, (EXEC_TIMEOUT_SEC, EXEC_TIMEOUT_SEC))
        except (ImportError, ValueError):
            pass

        _validate_script(script)

        safe_globals = _create_safe_builtins()
        safe_globals["__import__"] = _safe_import

        safe_cq = _SafeCadQueryProxy()
        safe_globals["cq"] = safe_cq
        safe_globals["cadquery"] = safe_cq

        import math
        safe_globals["math"] = math

        local_vars: dict[str, Any] = {}

        exec(script, safe_globals, local_vars)

        result = local_vars.get("result") or local_vars.get("solid") or local_vars.get("model") or local_vars.get("assembly")
        if result is None:
            for name, val in local_vars.items():
                if _is_assembly(val) or hasattr(val, "val") or hasattr(val, "toOCC"):
                    result = val
                    break

        if result is None:
            raise SandboxError("Script must define 'result', 'solid', 'model', or 'assembly'")

        multi_color = False
        if _is_assembly(result):
            multi_color = _export_assembly(result, out_glb)
        else:
            from cadquery import exporters
            stl_path = out_glb.replace(".glb", ".stl")
            exporters.export(result, stl_path)
            import trimesh
            mesh = trimesh.load_mesh(stl_path)
            mesh = _prepare_trimesh(mesh)
            mesh.export(out_glb, file_type="glb")
            try:
                Path(stl_path).unlink(missing_ok=True)
            except Exception:
                pass

        result_queue.put({"ok": True, "glb_path": out_glb, "multi_color": multi_color})

    except Exception as e:
        result_queue.put({"ok": False, "error": str(e), "error_type": type(e).__name__})


def _hex_to_rgb(color: str | None) -> tuple[int, int, int]:
    """Parse #RGB / #RRGGBB. Invalid input becomes default silver, never black."""
    raw = (color or "").strip()
    c = raw.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        return (192, 192, 192)
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return (192, 192, 192)
    return (r, g, b)


def _paint_glb(path: Path, color: str) -> None:
    import trimesh

    r, g, b = _hex_to_rgb(color)
    loaded = trimesh.load(str(path))
    if isinstance(loaded, trimesh.Scene):
        if not loaded.geometry:
            raise SandboxError("GLB scene has no geometry")
        for geom in loaded.geometry.values():
            geom.visual.vertex_colors = [r, g, b, 255]
        loaded.export(str(path), file_type="glb")
        return
    loaded.visual.vertex_colors = [r, g, b, 255]
    loaded.export(str(path), file_type="glb")


def _execute_sandboxed(
    script: str,
    output_dir: Path,
    timeout: float = EXEC_TIMEOUT_SEC,
    color: str = "#C0C0C0",
    flatten_color: bool = True,
) -> tuple[str, Path, float, bool]:
    """
    Internal: Execute script and return tuple (model_id, glb_path, exec_ms).
    Raises exceptions on failure.
    """
    model_id = uuid.uuid4().hex[:12]
    out_glb = output_dir / f"{model_id}.glb"

    t0 = time.perf_counter()

    result_queue = multiprocessing.Queue()
    proc = multiprocessing.Process(
        target=_run_in_sandbox,
        args=(script, str(out_glb), result_queue),
    )

    proc.start()
    proc.join(timeout=timeout)

    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=2)
        if proc.is_alive():
            proc.kill()
            proc.join()
        raise TimeoutError(f"Script execution timed out after {timeout}s")

    exec_ms = (time.perf_counter() - t0) * 1000

    try:
        result = result_queue.get_nowait()
    except Exception:
        raise SandboxError("Sandbox process terminated without result")

    if not result.get("ok"):
        error_type = result.get("error_type", "SandboxError")
        error_msg = result.get("error", "Unknown error")

        if error_type == "NonManifoldError":
            raise NonManifoldError(error_msg)
        elif error_type == "SecurityError":
            raise SecurityError(error_msg)
        else:
            raise SandboxError(error_msg)

    keep_parts = bool(result.get("multi_color")) and not flatten_color
    if not keep_parts:
        try:
            _paint_glb(out_glb, color)
        except Exception as e:
            logger.warning("Failed to apply color: %s", e)
    else:
        logger.info("Keeping per-part Assembly colors")

    logger.info("Sandbox exec completed in %.1fms → %s", exec_ms, out_glb.name)
    return model_id, out_glb, exec_ms, bool(result.get("multi_color"))


def execute_cadquery_script(
    script: str,
    output_dir: Path | str | None = None,
    timeout: float = EXEC_TIMEOUT_SEC,
    color: str = "#C0C0C0",
    flatten_color: bool = True,
) -> dict:
    """
    Execute CadQuery script in sandbox.

    PRIMARY PUBLIC API for Gemini codegen integration.

    Args:
        script: CadQuery Python script. Must define 'result', 'solid', 'model', or 'assembly'.
        output_dir: Directory for GLB output. Defaults to /tmp/perception_cad_glb.
        timeout: Max execution time in seconds (default 45).
        color: Hex color to apply when flattening (default silver #C0C0C0).
        flatten_color: If True, paint the whole GLB `color`. If False, keep
            Assembly per-part cq.Color values.

    Returns:
        dict with ok, glb_path, model_id, exec_ms, multi_color; or error fields.
    """
    if output_dir is None:
        output_dir = Path("/tmp/perception_cad_glb")
    elif isinstance(output_dir, str):
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()

    try:
        model_id, glb_path, exec_ms, multi_color = _execute_sandboxed(
            script=script,
            output_dir=output_dir,
            timeout=timeout,
            color=color,
            flatten_color=flatten_color,
        )
        return {
            "ok": True,
            "glb_path": str(glb_path),
            "model_id": model_id,
            "exec_ms": exec_ms,
            "multi_color": multi_color,
        }

    except TimeoutError as e:
        return {
            "ok": False,
            "error": str(e),
            "error_type": "timeout",
            "exec_ms": (time.perf_counter() - t0) * 1000,
        }

    except SecurityError as e:
        return {
            "ok": False,
            "error": str(e),
            "error_type": "security",
            "exec_ms": (time.perf_counter() - t0) * 1000,
        }

    except NonManifoldError as e:
        return {
            "ok": False,
            "error": str(e),
            "error_type": "non_manifold",
            "exec_ms": (time.perf_counter() - t0) * 1000,
        }

    except SandboxError as e:
        error_msg = str(e)
        error_type = "syntax" if "Syntax error" in error_msg else "execution"
        return {
            "ok": False,
            "error": error_msg,
            "error_type": error_type,
            "exec_ms": (time.perf_counter() - t0) * 1000,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "error_type": "execution",
            "exec_ms": (time.perf_counter() - t0) * 1000,
        }


# Aliases for convenience
execute_script = execute_cadquery_script
execute_cadquery = execute_cadquery_script
