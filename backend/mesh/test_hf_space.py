#!/usr/bin/env python3
"""HF Space payload helpers — no live GPU."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mesh.hf_space import first_glb_url
from mesh.meshy import MeshBusyError, MeshError
from mesh.hf_space import _parse_sse_block, _is_busy


def test_first_glb_url() -> tuple[int, int]:
    print("\n=== Test: first_glb_url walks Gradio payload ===")
    space = "https://trellis-community-trellis.hf.space"
    payload = [
        {"path": "/tmp/preview.mp4", "url": f"{space}/gradio_api/file=/tmp/preview.mp4"},
        {
            "path": "/tmp/gradio/out.glb",
            "url": f"{space}/gradio_api/file=/tmp/gradio/out.glb",
            "orig_name": "output.glb",
        },
    ]
    got = first_glb_url(space, payload)
    if got and got.endswith("out.glb"):
        print("  [ok]", got)
        return 1, 0
    print(f"  [FAIL] {got}")
    return 0, 1


def test_busy_quota() -> tuple[int, int]:
    print("\n=== Test: GPU quota is busy, not a hard fail ===")
    if not _is_busy("You have exceeded your GPU quota"):
        print("  [FAIL] quota should look busy")
        return 0, 1
    try:
        _parse_sse_block("error", '"You have exceeded your GPU quota"', "https://x")
    except MeshBusyError:
        print("  [ok] quota → MeshBusyError")
        return 1, 0
    except MeshError as exc:
        print(f"  [FAIL] expected busy, got MeshError {exc}")
        return 0, 1
    print("  [FAIL] expected raise")
    return 0, 1


def run_all_tests() -> int:
    print("=" * 60)
    print("HF SPACE TESTS")
    print("=" * 60)
    passed = failed = 0
    for fn in (test_first_glb_url, test_busy_quota):
        p, f = fn()
        passed += p
        failed += f
    print(f"\nTOTAL: {passed}/{passed + failed} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
