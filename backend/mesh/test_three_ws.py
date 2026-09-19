#!/usr/bin/env python3
"""three.ws image routing tests — no live Forge."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mesh.three_ws import _image_forge_body, _tier_for_quality, pick_image_backends


def test_tier_maps_high_off_hunyuan() -> tuple[int, int]:
    print("\n=== Test: high quality does not pin Hunyuan ===")
    if _tier_for_quality("high") != "standard":
        print("  [FAIL] high should map to standard, not hunyuan's high tier")
        return 0, 1
    if _tier_for_quality("draft") != "draft":
        print("  [FAIL] draft should stay draft")
        return 0, 1
    print("  [ok] quality → tier")
    return 1, 0


def test_pick_skips_down_prefers_ok() -> tuple[int, int]:
    print("\n=== Test: pick_image_backends skips down workers ===")
    health = {
        "status": "degraded",
        "backends": {
            "huggingface": {"status": "ok"},
            "trellis_selfhost": {"status": "degraded"},
            "hunyuan3d": {"status": "down"},
        },
    }
    got = pick_image_backends(health, "draft")
    if got[0] != "huggingface":
        print(f"  [FAIL] expected huggingface first, got {got}")
        return 0, 1
    if got[0] == "hunyuan3d":
        print(f"  [FAIL] down hunyuan should not lead, got {got}")
        return 0, 1
    if "trellis_selfhost" not in got:
        print(f"  [FAIL] self-host must stay in the walk, got {got}")
        return 0, 1
    print("  [ok]", got)
    return 1, 0


def test_pick_keeps_selfhost_when_marked_down() -> tuple[int, int]:
    print("\n=== Test: self-host stays in the walk even if health says down ===")
    health = {
        "backends": {
            "huggingface": {"status": "ok"},
            "trellis_selfhost": {"status": "down"},
            "hunyuan3d": {"status": "down"},
        }
    }
    got = pick_image_backends(health, "draft")
    if "trellis_selfhost" not in got:
        print(f"  [FAIL] expected self-host appended, got {got}")
        return 0, 1
    print("  [ok]", got)
    return 1, 0


def test_forge_body_uses_official_fields() -> tuple[int, int]:
    print("\n=== Test: Forge body uses image_urls + tier + backend ===")
    body = _image_forge_body(
        "https://example.com/p.png",
        tier="draft",
        backend="huggingface",
        prompt="pikachu",
    )
    if body.get("image_urls") != ["https://example.com/p.png"]:
        print(f"  [FAIL] image_urls {body.get('image_urls')}")
        return 0, 1
    if body.get("tier") != "draft" or body.get("backend") != "huggingface":
        print(f"  [FAIL] tier/backend {body}")
        return 0, 1
    if body.get("path") != "image":
        print(f"  [FAIL] path {body.get('path')}")
        return 0, 1
    print("  [ok] official Forge contract")
    return 1, 0


def run_all_tests() -> int:
    print("=" * 60)
    print("THREE.WS ROUTING TESTS")
    print("=" * 60)
    passed = failed = 0
    for fn in (
        test_tier_maps_high_off_hunyuan,
        test_pick_skips_down_prefers_ok,
        test_pick_keeps_selfhost_when_marked_down,
        test_forge_body_uses_official_fields,
    ):
        p, f = fn()
        passed += p
        failed += f
    print(f"\nTOTAL: {passed}/{passed + failed} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
