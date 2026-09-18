#!/usr/bin/env python3
"""Base-plate cleanup tests — synthetic GLBs, no live generator."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import trimesh

from mesh.cleanup import strip_base_plate


def _character_on_plate(dest: Path) -> None:
    """A body and two ears standing on a wide thin slab, plus plate debris."""
    plate = trimesh.creation.box(extents=(2.0, 0.05, 2.0))
    plate.apply_translation((0, 0.025, 0))

    body = trimesh.creation.icosphere(radius=0.3)
    body.apply_translation((0, 0.35, 0))
    ear_l = trimesh.creation.box(extents=(0.08, 0.3, 0.08))
    ear_l.apply_translation((-0.15, 0.75, 0))
    ear_r = trimesh.creation.box(extents=(0.08, 0.3, 0.08))
    ear_r.apply_translation((0.15, 0.75, 0))

    parts = [plate, body, ear_l, ear_r]
    # Shards the plate leaves behind, flat and in its own plane.
    for x, z in ((-0.95, -0.95), (0.95, 0.95), (-0.95, 0.9), (0.9, -0.85)):
        shard = trimesh.creation.box(extents=(0.05, 0.004, 0.05))
        shard.apply_translation((x, 0.002, z))
        parts.append(shard)

    trimesh.Scene(trimesh.util.concatenate(parts)).export(str(dest))


def _flat_coaster(dest: Path) -> None:
    """An object that is legitimately all slab — cleanup must not eat it."""
    disc = trimesh.creation.cylinder(radius=1.0, height=0.06)
    disc.apply_transform(trimesh.transformations.rotation_matrix(1.5708, (1, 0, 0)))
    trimesh.Scene(disc).export(str(dest))


def test_plate_removed(tmp_path: Path) -> tuple[int, int]:
    print("\n=== Test: ground plate is stripped ===")
    glb = tmp_path / "character.glb"
    _character_on_plate(glb)
    before = trimesh.load(str(glb), force="mesh").extents

    info = strip_base_plate(glb)
    after = trimesh.load(str(glb), force="mesh")
    width = max(after.extents[0], after.extents[2])

    if not info.get("stripped"):
        print(f"  [FAIL] nothing stripped: {info}")
        return 0, 1
    if width > 0.8:
        print(f"  [FAIL] plate survived: {before} -> {after.extents}")
        return 0, 1
    if after.extents[1] < 0.7:
        print(f"  [FAIL] ears were culled with the plate: {after.extents}")
        return 0, 1
    print(f"  [ok] {[round(x, 2) for x in before]} -> {[round(x, 2) for x in after.extents]}")
    return 1, 0


def test_model_is_grounded(tmp_path: Path) -> tuple[int, int]:
    print("\n=== Test: stripped model sits on y=0, centered in x/z ===")
    glb = tmp_path / "grounded.glb"
    _character_on_plate(glb)
    strip_base_plate(glb)
    lo, hi = trimesh.load(str(glb), force="mesh").bounds

    if abs(lo[1]) > 1e-3:
        print(f"  [FAIL] not grounded, min y = {lo[1]}")
        return 0, 1
    if abs(lo[0] + hi[0]) > 1e-3 or abs(lo[2] + hi[2]) > 1e-3:
        print(f"  [FAIL] not centered: {lo} {hi}")
        return 0, 1
    print("  [ok] grounded and centered")
    return 1, 0


def test_flat_object_survives(tmp_path: Path) -> tuple[int, int]:
    print("\n=== Test: a genuinely flat model is left alone ===")
    glb = tmp_path / "coaster.glb"
    _flat_coaster(glb)
    before = len(trimesh.load(str(glb), force="mesh").faces)

    strip_base_plate(glb)
    after = len(trimesh.load(str(glb), force="mesh").faces)

    if after != before:
        print(f"  [FAIL] coaster lost faces: {before} -> {after}")
        return 0, 1
    print("  [ok] coaster untouched")
    return 1, 0


def test_idempotent(tmp_path: Path) -> tuple[int, int]:
    print("\n=== Test: second pass is a no-op ===")
    glb = tmp_path / "twice.glb"
    _character_on_plate(glb)
    strip_base_plate(glb)
    faces = len(trimesh.load(str(glb), force="mesh").faces)

    again = strip_base_plate(glb)
    after = len(trimesh.load(str(glb), force="mesh").faces)

    if again.get("stripped") or after != faces:
        print(f"  [FAIL] second pass changed the model: {again}")
        return 0, 1
    print("  [ok] no-op")
    return 1, 0


def run_all_tests() -> int:
    import tempfile

    print("=" * 60)
    print("MESH CLEANUP TESTS")
    print("=" * 60)
    passed = failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for test in (
            test_plate_removed,
            test_model_is_grounded,
            test_flat_object_survives,
            test_idempotent,
        ):
            p, f = test(Path(tmp))
            passed += p
            failed += f
    print(f"\nTOTAL: {passed}/{passed + failed} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
