#!/usr/bin/env python3
"""Mesh factory tests — mocked three.ws / NVIDIA, no live GPU."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from mesh.factory import generate_mesh_glb, generate_mesh_glb_from_image, mesh_ready
from mesh.meshy import MeshBusyError, MeshError


def test_three_ws_first(tmp_path: Path) -> tuple[int, int]:
    print("\n=== Test: factory prefers three.ws ===")

    async def fake_three(prompt, dest, timeout_s=90.0):
        dest.write_bytes(b"glb-three")
        return {"ok": True, "textured": True, "provider": "three_ws"}

    async def boom(*_a, **_k):
        raise AssertionError("NVIDIA should not run when three.ws works")

    with patch("mesh.factory.generate_three_ws_glb", fake_three):
        with patch("mesh.factory.generate_nvidia_glb", boom):
            result = asyncio.run(
                generate_mesh_glb(
                    "a corgi",
                    tmp_path,
                    nvidia_api_key="nv-key",
                    meshy_api_key="",
                    three_ws=True,
                )
            )
    if result.get("ok") and result.get("provider") == "three_ws":
        print("  [ok] three.ws wins")
        return 1, 0
    print(f"  [FAIL] {result}")
    return 0, 1


def test_nvidia_when_three_ws_fails(tmp_path: Path) -> tuple[int, int]:
    print("\n=== Test: factory falls back to NVIDIA ===")

    async def fail_three(*_a, **_k):
        raise MeshError("rate limited")

    async def fake_nv(prompt, dest, api_key, timeout_s=120.0):
        dest.write_bytes(b"glb-nv")
        return {"ok": True, "textured": True, "provider": "nvidia"}

    with patch("mesh.factory.generate_three_ws_glb", fail_three):
        with patch("mesh.factory.generate_nvidia_glb", fake_nv):
            result = asyncio.run(
                generate_mesh_glb(
                    "a dragon",
                    tmp_path,
                    nvidia_api_key="nv-key",
                    three_ws=True,
                )
            )
    if result.get("ok") and result.get("provider") == "nvidia":
        print("  [ok] NVIDIA fallback")
        return 1, 0
    print(f"  [FAIL] {result}")
    return 0, 1


def test_mesh_ready_keyless() -> tuple[int, int]:
    print("\n=== Test: mesh_ready with no paid keys ===")
    s = SimpleNamespace(meshy_api_key="", nvidia_api_key="", three_ws_enabled=True)
    if mesh_ready(s):
        print("  [ok] three.ws counts as ready")
        return 1, 0
    print("  [FAIL] expected ready")
    return 0, 1


def test_image_prefers_hf_space(tmp_path: Path) -> tuple[int, int]:
    print("\n=== Test: image factory prefers HF Space ===")
    photo = tmp_path / "ref.png"
    photo.write_bytes(b"\x89PNG\r\n" + b"x" * 32)

    async def fake_hf(image_path, dest, token="", timeout_s=240.0):
        dest.write_bytes(b"glb-hf")
        return {"ok": True, "textured": True, "provider": "hf_space"}

    async def boom(*_a, **_k):
        raise AssertionError("three.ws should not run when HF Space works")

    with patch("mesh.factory.generate_hf_space_glb", fake_hf):
        with patch("mesh.factory.generate_three_ws_glb_from_image", boom):
            result = asyncio.run(
                generate_mesh_glb_from_image(
                    "https://example.com/p.png",
                    tmp_path,
                    image_path=photo,
                )
            )
    if result.get("ok") and result.get("provider") == "hf_space":
        print("  [ok] HF Space wins")
        return 1, 0
    print(f"  [FAIL] {result}")
    return 0, 1


def test_image_falls_back_to_three_ws(tmp_path: Path) -> tuple[int, int]:
    print("\n=== Test: image factory falls back to three.ws ===")
    photo = tmp_path / "ref.png"
    photo.write_bytes(b"\x89PNG\r\n" + b"x" * 32)

    async def busy_hf(*_a, **_k):
        raise MeshBusyError("gpu quota")

    async def fake_three(image_url, dest, prompt="", timeout_s=300.0, quality="draft"):
        dest.write_bytes(b"glb-three")
        return {"ok": True, "textured": True, "provider": "three_ws"}

    with patch("mesh.factory.generate_hf_space_glb", busy_hf):
        with patch("mesh.factory.generate_three_ws_glb_from_image", fake_three):
            result = asyncio.run(
                generate_mesh_glb_from_image(
                    "https://example.com/p.png",
                    tmp_path,
                    image_path=photo,
                )
            )
    if result.get("ok") and result.get("provider") == "three_ws_image":
        print("  [ok] three.ws fallback")
        return 1, 0
    print(f"  [FAIL] {result}")
    return 0, 1


def run_all_tests() -> int:
    import tempfile

    print("=" * 60)
    print("MESH FACTORY TESTS")
    print("=" * 60)
    passed = failed = 0
    p, f = test_mesh_ready_keyless()
    passed += p
    failed += f
    with tempfile.TemporaryDirectory() as tmp:
        p, f = test_three_ws_first(Path(tmp))
        passed += p
        failed += f
        p, f = test_nvidia_when_three_ws_fails(Path(tmp))
        passed += p
        failed += f
        p, f = test_image_prefers_hf_space(Path(tmp))
        passed += p
        failed += f
        p, f = test_image_falls_back_to_three_ws(Path(tmp))
        passed += p
        failed += f
    print(f"\nTOTAL: {passed}/{passed + failed} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
