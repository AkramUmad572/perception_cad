"""
Sandboxed CadQuery script execution.

Public API:
    execute_cadquery_script(script: str) -> { ok, glb_path | error }

Provides safe execution of generated CadQuery scripts with:
- Hard timeout (kills hung exec)
- No filesystem access (blocked builtins)
- No network access (no socket/urllib)
- Non-manifold mesh rejection
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

EXEC_TIMEOUT_SEC = 30
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

        result = local_vars.get("result") or local_vars.get("solid") or local_vars.get("model")
        if result is None:
            for name, val in local_vars.items():
                if hasattr(val, "val") or hasattr(val, "toOCC"):
                    result = val
                    break

        if result is None:
            raise SandboxError("Script must define 'result', 'solid', or 'model' variable")

        from cadquery import exporters
        stl_path = out_glb.replace(".glb", ".stl")
        exporters.export(result, stl_path)

        import trimesh
        mesh = trimesh.load_mesh(stl_path)
        if isinstance(mesh, trimesh.Scene):
            mesh = trimesh.util.concatenate(tuple(mesh.geometry.values()))

        _check_mesh_limits(mesh)

        if not _check_manifold(mesh):
            logger.warning("Non-manifold mesh detected, attempting repair")
            try:
                trimesh.repair.fix_normals(mesh)
                trimesh.repair.fill_holes(mesh)
                if not _check_manifold(mesh):
                    raise NonManifoldError("Generated mesh is non-manifold and could not be repaired")
            except Exception as e:
                raise NonManifoldError(f"Mesh repair failed: {e}")

        mesh.apply_scale(0.001)
        mesh.export(out_glb, file_type="glb")

        try:
            Path(stl_path).unlink(missing_ok=True)
        except Exception:
            pass

        result_queue.put({"ok": True, "glb_path": out_glb})

    except Exception as e:
        result_queue.put({"ok": False, "error": str(e), "error_type": type(e).__name__})


def _execute_sandboxed(
    script: str,
    output_dir: Path,
    timeout: float = EXEC_TIMEOUT_SEC,
    color: str = "#C0C0C0",
) -> tuple[str, Path, float]:
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

    if color and color != "#C0C0C0":
        try:
            import trimesh
            mesh = trimesh.load(str(out_glb), force="mesh")
            c = color.lstrip("#")
            if len(c) == 3:
                c = "".join(ch * 2 for ch in c)
            r = int(c[0:2], 16)
            g = int(c[2:4], 16)
            b = int(c[4:6], 16)
            mesh.visual.vertex_colors = [r, g, b, 255]
            mesh.export(str(out_glb), file_type="glb")
        except Exception as e:
            logger.warning("Failed to apply color: %s", e)

    logger.info("Sandbox exec completed in %.1fms → %s", exec_ms, out_glb.name)
    return model_id, out_glb, exec_ms


def execute_cadquery_script(
    script: str,
    output_dir: Path | str | None = None,
    timeout: float = EXEC_TIMEOUT_SEC,
    color: str = "#C0C0C0",
) -> dict:
    """
    Execute CadQuery script in sandbox.

    PRIMARY PUBLIC API for Gemini codegen integration.

    Args:
        script: CadQuery Python script. Must define 'result', 'solid', or 'model'.
        output_dir: Directory for GLB output. Defaults to /tmp/perception_cad_glb.
        timeout: Max execution time in seconds (default 30).
        color: Hex color to apply (default silver #C0C0C0).

    Returns:
        dict:
            ok: bool - True if successful
            glb_path: str - Absolute path to GLB file (only if ok=True)
            model_id: str - Unique model identifier (only if ok=True)
            exec_ms: float - Execution time in milliseconds
            error: str - Error message (only if ok=False)
            error_type: str - One of: 'timeout', 'security', 'non_manifold', 'syntax', 'execution'

    Example:
        >>> from cad.sandbox import execute_cadquery_script
        >>> result = execute_cadquery_script(
        ...     script='import cadquery as cq\\nresult = cq.Workplane("XY").box(10, 10, 5)',
        ... )
        >>> if result["ok"]:
        ...     print(f"GLB at: {result['glb_path']}")
        ... else:
        ...     print(f"Failed ({result['error_type']}): {result['error']}")

    Safety:
        - 30s hard timeout (kills hung processes)
        - No filesystem access (open, pathlib, shutil blocked)
        - No network access (socket, urllib, requests blocked)
        - No code injection (exec, eval, compile, __import__ blocked)
        - Non-manifold mesh rejection (with repair attempt)
        - Memory limit: 2GB
        - Vertex/face limits: 500k each
    """
    if output_dir is None:
        output_dir = Path("/tmp/perception_cad_glb")
    elif isinstance(output_dir, str):
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()

    try:
        model_id, glb_path, exec_ms = _execute_sandboxed(
            script=script,
            output_dir=output_dir,
            timeout=timeout,
            color=color,
        )
        return {
            "ok": True,
            "glb_path": str(glb_path),
            "model_id": model_id,
            "exec_ms": exec_ms,
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
