"""
CAD module for Perception CAD.

Primary API for FREE-REIN codegen integration:

    from cad import execute_cadquery

    # Gemini/LLM generates ANY CadQuery script from user utterance
    result = execute_cadquery(
        script='import cadquery as cq\\nresult = cq.Workplane("XY").sphere(15)',
        output_dir="/path/to/glb",
        timeout=30,
        color="#FFD700",
    )

    if result["ok"]:
        print(f"GLB at: {result['glb_path']}")
    else:
        # On failure, feed error back to LLM for repair (see ai.intent.repair_and_retry)
        print(f"Failed ({result['error_type']}): {result['error']}")

Flow: wake → listen → Gemini codegen → execute_cadquery → retry on failure → GLB
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
