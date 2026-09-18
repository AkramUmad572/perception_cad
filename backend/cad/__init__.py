"""
CAD module for Perception CAD.

Primary API for FREE-REIN codegen integration:

    from cad.sandbox import execute_cadquery_script

    # Gemini/LLM generates ANY CadQuery script from user utterance
    result = execute_cadquery_script(
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

Also available as:
    from cad.sandbox import execute_script  # alias
    from cad.sandbox import execute_cadquery  # alias

Flow: hold-to-talk → Gemini codegen → execute_cadquery_script → retry on failure → GLB
"""

from cad.sandbox import (
    # Primary API
    execute_cadquery_script,
    # Aliases
    execute_script,
    execute_cadquery,
    # Exceptions
    SandboxError,
    TimeoutError,
    NonManifoldError,
    SecurityError,
    # Constants
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
    "execute_cadquery_script",
    "execute_script",
    "execute_cadquery",
    # Legacy template builder
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
