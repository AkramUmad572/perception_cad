"""Mesh factories for organic / character GLBs. CadQuery stays in cad/."""

from mesh.factory import generate_mesh_glb, mesh_providers, mesh_ready
from mesh.meshy import MeshError

__all__ = ["generate_mesh_glb", "mesh_ready", "mesh_providers", "MeshError"]
