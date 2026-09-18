#!/usr/bin/env python3
"""Meshy client tests — mocked HTTP, no live API."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from mesh.meshy import generate_mesh_glb


class _Resp:
    def __init__(self, payload=None, content=b"", status_code=200):
        self._payload = payload or {}
        self.content = content
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            req = httpx.Request("GET", "https://api.meshy.ai/openapi/v2/text-to-3d")
            raise httpx.HTTPStatusError("err", request=req, response=self)

    def json(self):
        return self._payload


def test_preview_and_refine_saves_glb(tmp_path: Path) -> tuple[int, int]:
    print("\n=== Test: Meshy preview + refine downloads GLB ===")
    calls = {"post": 0, "get": 0}

    async def fake_post(url, headers=None, json=None):
        calls["post"] += 1
        mode = (json or {}).get("mode")
        if mode == "preview":
            return _Resp({"result": "prev-1"})
        if mode == "refine":
            return _Resp({"result": "ref-1"})
        raise AssertionError(f"unexpected post {json}")

    async def fake_get(url, headers=None, follow_redirects=None):
        calls["get"] += 1
        if url.endswith("/prev-1"):
            return _Resp(
                {
                    "status": "SUCCEEDED",
                    "model_urls": {"glb": "https://assets.example/preview.glb"},
                }
            )
        if url.endswith("/ref-1"):
            return _Resp(
                {
                    "status": "SUCCEEDED",
                    "model_urls": {"glb": "https://assets.example/refined.glb"},
                }
            )
        if url.endswith("refined.glb"):
            return _Resp(content=b"glb-bytes")
        raise AssertionError(f"unexpected get {url}")

    fake_client = MagicMock()
    fake_client.post = AsyncMock(side_effect=fake_post)
    fake_client.get = AsyncMock(side_effect=fake_get)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    with patch("mesh.meshy.httpx.AsyncClient", return_value=fake_client):
        result = asyncio.run(
            generate_mesh_glb("a yellow mouse", tmp_path, api_key="test-key", timeout_s=30)
        )

    errors = []
    if not result.get("ok"):
        errors.append(f"expected ok, got {result}")
    if not result.get("textured"):
        errors.append("refine should mark textured=True")
    glb = Path(result.get("glb_path") or "")
    if not glb.exists() or glb.read_bytes() != b"glb-bytes":
        errors.append("GLB not written")
    if calls["post"] != 2:
        errors.append(f"expected preview+refine posts, got {calls['post']}")

    if errors:
        print("  [FAIL]")
        for e in errors:
            print(f"    - {e}")
        return 0, 1
    print("  [ok] preview + refine saved GLB")
    return 1, 0


def test_missing_key_does_not_call_network(tmp_path: Path) -> tuple[int, int]:
    print("\n=== Test: missing MESHY key does not hit network ===")
    with patch("mesh.meshy.httpx.AsyncClient") as client:
        result = asyncio.run(generate_mesh_glb("dragon", tmp_path, api_key=""))
    if result.get("ok") or client.called:
        print("  [FAIL] should fail closed without HTTP")
        return 0, 1
    print("  [ok] config error, no HTTP")
    return 1, 0


def run_all_tests() -> int:
    import tempfile

    print("=" * 60)
    print("MESHY CLIENT TESTS")
    print("=" * 60)
    passed = failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        p, f = test_preview_and_refine_saves_glb(Path(tmp))
        passed += p
        failed += f
        p, f = test_missing_key_does_not_call_network(Path(tmp))
        passed += p
        failed += f
    print(f"\nTOTAL: {passed}/{passed + failed} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
