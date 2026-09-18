"""Strip the ground plate text-to-3D generators bake into their output."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# A slab counts as ground when it is far flatter than the model and covers most
# of the footprint. TRELLIS character parts top out near 45% coverage, so the
# gap between "base plate" and "body part" is wide.
FLATNESS_MAX = 0.10
FOOTPRINT_MIN = 0.55
# Never let cleanup eat the model itself: a genuinely flat request (a coaster,
# a nameplate) is all slab and must survive untouched.
KEEP_FACES_MIN = 0.35
# A plate rarely comes off as one piece; it leaves hundreds of pancake shards
# in its own plane. They are only ~15% of the faces but they reach the old plate
# corners, so they keep the bounding box huge and the model gets scaled down to
# nothing. Anything living entirely in that thin floor band is residue.
FLOOR_BAND = 0.03
# Generators also strand needles and shells far outside the subject. They are a
# small share of the faces but they dominate the bounding box, and the client
# scales by the longest axis — so the model arrives tiny.
CORE_FACE_SHARE = 0.50
CORE_PAD = 0.20
CORE_PAD_FLOOR = 0.05
UP_AXIS = 1  # glTF is Y-up


def _face_groups(mesh: Any) -> list[Any]:
    import numpy as np
    import trimesh

    if len(mesh.faces) == 0:
        return []
    return trimesh.graph.connected_components(
        mesh.face_adjacency, nodes=np.arange(len(mesh.faces))
    )


def _extents(mesh: Any, faces: Any) -> Any:
    import numpy as np

    verts = mesh.vertices[np.unique(mesh.faces[faces])]
    return verts.min(axis=0), verts.max(axis=0)


def _is_ground_slab(
    lo: Any, hi: Any, model_lo: Any, model_hi: Any, floor_cut: float
) -> bool:
    size = hi - lo
    if size.max() <= 0:
        return False
    if size.min() / size.max() > FLATNESS_MAX:
        return False

    model_size = model_hi - model_lo
    footprint = [i for i in range(3) if i != UP_AXIS]
    for axis in footprint:
        span = model_size[axis]
        if span <= 0 or size[axis] / span < FOOTPRINT_MIN:
            return False

    # Must sit at the bottom — a flat brim or hat disc partway up is geometry.
    return hi[UP_AXIS] <= floor_cut


def _floor_residue(kept: list[Any], model_lo: Any, model_hi: Any) -> set[int]:
    """Indices of shards left lying in the plane the plate occupied."""
    height = model_hi[UP_AXIS] - model_lo[UP_AXIS]
    if height <= 0:
        return set()
    band_top = model_lo[UP_AXIS] + height * FLOOR_BAND
    return {i for i, (_f, _lo, hi) in enumerate(kept) if hi[UP_AXIS] <= band_top}


def _far_outliers(kept: list[Any], model_lo: Any, model_hi: Any) -> list[Any]:
    """Components stranded outside the bulk of the model."""
    import numpy as np

    if len(kept) < 2:
        return []

    ranked = sorted(kept, key=lambda k: -len(k[0]))
    total = sum(len(faces) for faces, _lo, _hi in ranked)
    core: list[Any] = []
    seen = 0
    for entry in ranked:
        core.append(entry)
        seen += len(entry[0])
        if seen >= total * CORE_FACE_SHARE:
            break
    if len(core) == len(ranked):
        return []

    core_lo = np.min([lo for _f, lo, _hi in core], axis=0)
    core_hi = np.max([hi for _f, _lo, hi in core], axis=0)
    # Pad relative to the whole model too, so a paper-thin axis keeps a usable
    # tolerance instead of rejecting the subject's own front and back shells.
    pad = np.maximum(
        (core_hi - core_lo) * CORE_PAD, (model_hi - model_lo) * CORE_PAD_FLOOR
    )
    core_lo, core_hi = core_lo - pad, core_hi + pad

    outliers = []
    for faces, lo, hi in ranked[len(core):]:
        if np.any(hi < core_lo) or np.any(lo > core_hi):
            outliers.append(faces)
    return outliers


def strip_base_plate(glb_path: Path | str) -> dict[str, Any]:
    """
    Drop flat ground slabs from a generated GLB and recenter what is left.

    Rewrites the file in place. Textures survive because faces are masked on the
    existing geometry rather than rebuilt.
    """
    path = Path(glb_path)
    try:
        import numpy as np
        import trimesh
    except ImportError:
        return {"stripped": False, "reason": "trimesh unavailable"}

    try:
        scene = trimesh.load(str(path))
    except Exception as exc:
        logger.warning("Base-plate cleanup could not load %s: %s", path.name, exc)
        return {"stripped": False, "reason": str(exc)}

    if isinstance(scene, trimesh.Trimesh):
        scene = trimesh.Scene(scene)
    if not isinstance(scene, trimesh.Scene) or not scene.geometry:
        return {"stripped": False, "reason": "no geometry"}

    removed_faces = 0
    total_faces = 0
    changed = False

    for name, mesh in scene.geometry.items():
        if not isinstance(mesh, trimesh.Trimesh):
            continue
        total_faces += len(mesh.faces)
        groups = _face_groups(mesh)
        if len(groups) < 2:
            continue

        # Compare in the geometry's own frame; scene bounds include transforms.
        model_lo, model_hi = mesh.bounds
        floor_cut = model_lo[UP_AXIS] + (model_hi[UP_AXIS] - model_lo[UP_AXIS]) * 0.25

        drop = np.zeros(len(mesh.faces), dtype=bool)
        kept: list[Any] = []
        for faces in groups:
            lo, hi = _extents(mesh, faces)
            if _is_ground_slab(lo, hi, model_lo, model_hi, floor_cut):
                drop[faces] = True
            else:
                kept.append((faces, lo, hi))

        if drop.any():
            residue = _floor_residue(kept, model_lo, model_hi)
            for i in residue:
                drop[kept[i][0]] = True
            kept = [k for i, k in enumerate(kept) if i not in residue]

        for faces in _far_outliers(kept, model_lo, model_hi):
            drop[faces] = True

        if not drop.any() or drop.all():
            continue
        keep_ratio = float((~drop).sum()) / len(mesh.faces)
        if keep_ratio < KEEP_FACES_MIN:
            logger.info(
                "Cleanup skipped %s: would remove %.0f%% of the mesh",
                name,
                (1 - keep_ratio) * 100,
            )
            continue

        removed_faces += int(drop.sum())
        mesh.update_faces(~drop)
        mesh.remove_unreferenced_vertices()
        changed = True

    if not changed:
        return {"stripped": False, "reason": "nothing to strip"}

    # Recenter on the footprint and sit the model on y=0 so it stands upright.
    meshes = [m for m in scene.geometry.values() if isinstance(m, trimesh.Trimesh)]
    lo = np.min([m.bounds[0] for m in meshes], axis=0)
    hi = np.max([m.bounds[1] for m in meshes], axis=0)
    offset = np.array(
        [
            -(lo[0] + hi[0]) / 2.0,
            -lo[UP_AXIS],
            -(lo[2] + hi[2]) / 2.0,
        ]
    )
    for mesh in meshes:
        mesh.apply_translation(offset)

    try:
        path.write_bytes(trimesh.exchange.gltf.export_glb(scene))
    except Exception as exc:
        logger.warning("Base-plate cleanup could not export %s: %s", path.name, exc)
        return {"stripped": False, "reason": str(exc)}

    logger.info("Cleaned %s: -%d/%d faces", path.name, removed_faces, total_faces)
    return {"stripped": True, "removed_faces": removed_faces, "total_faces": total_faces}
