"""
Sandboxed CadQuery script execution.

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
import signal
import tempfile
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
    """Restricted import that blocks dangerous modules."""
    base_module = name.split(".")[0]
    if base_module in BLOCKED_MODULES:
        raise SecurityError(f"Import of '{name}' is blocked in sandbox")

    if fromlist:
        for item in fromlist:
            if item in BLOCKED_MODULES:
                raise SecurityError(f"Import of '{item}' from '{name}' is blocked")

    import importlib
    return importlib.import_module(name)


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
                if base in BLOCKED_MODULES:
                    raise SecurityError(f"Import of '{alias.name}' is blocked")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                base = node.module.split(".")[0]
                if base in BLOCKED_MODULES:
                    raise SecurityError(f"Import from '{node.module}' is blocked")


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

        import cadquery as cq
        safe_globals["cq"] = cq
        safe_globals["cadquery"] = cq

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


def execute_cadquery_script(
    script: str,
    output_dir: Path,
    timeout: float = EXEC_TIMEOUT_SEC,
    color: str = "#C0C0C0",
) -> tuple[str, Path, float]:
    """
    Execute a CadQuery script in a sandbox and return GLB.

    Args:
        script: CadQuery Python script to execute
        output_dir: Directory to write GLB output
        timeout: Maximum execution time in seconds
        color: Hex color to apply to mesh

    Returns:
        Tuple of (model_id, glb_path, exec_time_ms)

    Raises:
        SandboxError: If execution fails
        TimeoutError: If execution times out
        NonManifoldError: If mesh is non-manifold
        SecurityError: If script attempts blocked operations
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
