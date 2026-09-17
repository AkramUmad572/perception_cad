"""
CAD module for Perception CAD.

Primary API for codegen integration:
    from cad import execute_cadquery

    result = execute_cadquery(
        script='import cadquery as cq\\nresult = cq.Workplane("XY").box(10, 10, 5)',
        output_dir="/path/to/glb",
        timeout=30,
        color="#FFD700",
    )

    if result["ok"]:
        print(f"GLB at: {result['glb_path']}")
    else:
        print(f"Failed ({result['error_type']}): {result['error']}")
"""

from cad.sandbox import (
    execute_cadquery,
    execute_cadquery_script,
    SandboxError,
    TimeoutError,
    NonManifoldError,
    SecurityError,
    EXEC_TIMEOUT_SEC,
)

from cad.builder import (
    build_model,
    build_from_script,
    build_any,
    merge_params,
    DEFAULTS,
)

__all__ = [
    # Primary API for codegen
    "execute_cadquery",
    # Lower-level APIs
    "execute_cadquery_script",
    "build_model",
    "build_from_script",
    "build_any",
    "merge_params",
    # Constants
    "DEFAULTS",
    "EXEC_TIMEOUT_SEC",
    # Exceptions
    "SandboxError",
    "TimeoutError",
    "NonManifoldError",
    "SecurityError",
]
