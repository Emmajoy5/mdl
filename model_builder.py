"""Builds the procedural 3D files for a strain: one place used by the web app and
by the generate_models.py command-line tool, so both produce the same models.

Colony: the viewer scene (GLB: glass dish, agar, colonies) and the printable
relief (STL) come from one colony layout (scene3d.colony_layout), so what is
printed is what is shown. Cell: a cell in its typical arrangement, scaled for
printing; the real size is reported in the note.
"""
import microbe3d as m3
import scene3d
import shape_config

COLONY_GRID = 512        # height-map resolution for the colony STL (0.18 mm per cell)


def build_colony_files(shape, seed, stl_path, glb_path, diameter_mm=90.0):
    """Returns (watertight, note). Colony counts in the shape config are for a
    90 mm plate; a smaller dish gets proportionally fewer, at the same sizes."""
    d = float(diameter_mm or 90.0)
    shape = dict(shape)
    shape["n_colonies"] = max(3, int(round(int(shape.get("n_colonies", 40)) * (d / 90.0) ** 2)))
    with scene3d.dish_size(d):
        height = scene3d.colony_height_map(shape, seed, n=COLONY_GRID, diameter_mm=d)
        top = float(height.max())
        rel = height / top if top > 0 else height
        mesh = m3.photo_colony_plate(rel, diameter_mm=d, relief_mm=max(top, 0.01),
                                     n_r=128, n_theta=256)
        m3.write_stl_binary(mesh, stl_path)
        scene3d.write_colony_scene(glb_path, shape, seed)
    lo, hi = shape.get("colony_radius_mm", (1.0, 2.5))
    note = (f"Procedural colony plate; colonies about {2 * lo:.1f} to {2 * hi:.1f} mm across "
            f"on a {d:g} mm dish; STL is the same layout.")
    return mesh.is_watertight(), note


def build_cell_files(shape, stl_path, glb_path):
    """Returns (watertight, note)."""
    mesh, scale = m3.cell_assembly(shape)
    m3.write_stl_binary(mesh, stl_path)
    scene3d.write_cell_scene(glb_path, mesh, tuple(shape.get("color", (0.92, 0.90, 0.85))))
    size = shape.get("size_um")
    what = shape_config.describe_cell(shape)
    basis = shape_config.BASIS_TEXT.get(shape.get("basis"), "")
    note = (f"Shown as {what}" + (f" ({basis})" if basis else "") +
            f"; model scaled x{scale * 1000:.0f} (1 mm = {1 / scale:.2f} µm)" +
            (f"; size: {size}" if size and size != "unknown" else ""))
    return mesh.is_watertight(), note
