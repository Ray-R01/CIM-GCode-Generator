"""DXF/SVG 2D contour import and STL/OBJ mesh preview helpers."""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import numpy as np
from shapely.affinity import scale as scale_geometry
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union


def _build_polygons(lines: list[LineString]):
    lines = [line for line in lines if len(line.coords) >= 2 and line.length > 1e-8]
    if not lines:
        raise ValueError("No usable linework was found in the file.")
    # unary_union nodes intersections / coincident endpoints; polygonize extracts loops.
    polygons = list(polygonize(unary_union(lines)))
    polygons = [p for p in polygons if p.area > 1e-8 and p.is_valid]
    if not polygons:
        raise ValueError(
            "No closed contours were found. Close the profile in CAD and export as DXF/SVG; "
            "open or disconnected linework cannot be turned into a safe contour automatically."
        )
    # polygonize returns both a polygon-with-hole and the nested ring as a second polygon.
    # Keep only top-level shells; retaining the shell's interior ring preserves circular/polygonal holes.
    roots = []
    for candidate in polygons:
        representative = candidate.representative_point()
        contained_by_larger_shell = False
        for other in polygons:
            if other is candidate or other.area <= candidate.area + 1e-8:
                continue
            exterior_only = Polygon(other.exterior)
            if exterior_only.covers(representative):
                contained_by_larger_shell = True
                break
        if not contained_by_larger_shell:
            roots.append(candidate)
    return unary_union(roots)


def load_dxf(data: bytes):
    """Import closed DXF outlines, including block INSERTs, and return polygon geometry.

    Many CAD exports store contours inside blocks or as splines/arcs rather than LINEs.
    We recursively expand INSERT entities and report what was actually found if no loops
    can be assembled. Only closed contours are accepted for machining.
    """
    try:
        import ezdxf
        from ezdxf.path import make_path
    except ImportError as exc:
        raise RuntimeError("DXF import needs ezdxf. Run: pip install -r requirements.txt") from exc

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as temp:
            temp.write(data)
            temp_path = temp.name
        doc = ezdxf.readfile(temp_path)
        msp = doc.modelspace()
        entities = []
        counts = {}
        def collect(entity, depth=0):
            kind = entity.dxftype()
            counts[kind] = counts.get(kind, 0) + 1
            if kind == "INSERT" and depth < 8:
                try:
                    for child in entity.virtual_entities():
                        collect(child, depth + 1)
                    return
                except Exception:
                    pass
            entities.append(entity)
        for ent in msp:
            collect(ent)

        supported = {"LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "ELLIPSE", "SPLINE"}
        lines: list[LineString] = []
        converted = 0
        for entity in entities:
            if entity.dxftype() not in supported:
                continue
            try:
                path = make_path(entity)
                points = [(float(p.x), float(p.y)) for p in path.flattening(distance=0.05, segments=16)]
                if len(points) >= 2:
                    if getattr(path, "is_closed", False) and points[0] != points[-1]:
                        points.append(points[0])
                    lines.append(LineString(points))
                    converted += 1
            except Exception:
                continue
        try:
            return _build_polygons(lines)
        except ValueError as exc:
            summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "no model-space entities"
            if not lines:
                raise ValueError(
                    "No usable linework was extracted from this DXF. Entities found: " + summary + ". "
                    "Try exporting the part from model space as a 2D DXF (R12 or 2010), explode blocks, "
                    "and convert text/dimensions/hatches to closed polylines. If this is a 3D drawing, "
                    "create a top-view 2D projection first."
                ) from exc
            raise ValueError(
                f"Found {converted} curve/line entities ({summary}), but they do not form a closed contour. "
                "Join endpoints / close the profile in CAD, or use the open-line preview/converter workflow."
            ) from exc
    except Exception as exc:
        if isinstance(exc, (ValueError, RuntimeError)):
            raise
        raise ValueError(f"Could not read DXF: {exc}") from exc
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def load_svg(data: bytes):
    """Import SVG paths as 2D contours. SVG drawing coordinates are used as mm units.

    SVG transforms and CSS styling are not interpreted as CAD dimensions. Export a flat,
    untransformed SVG with coordinates at 1 unit = 1 mm for best results.
    """
    try:
        from svgpathtools import svgstr2paths
    except ImportError as exc:
        raise RuntimeError("SVG import needs svgpathtools. Run: pip install -r requirements.txt") from exc

    try:
        text = data.decode("utf-8-sig")
        paths, _attributes, _svg_attributes = svgstr2paths(text, return_svg_attributes=True)
        lines: list[LineString] = []
        for path in paths:
            points: list[tuple[float, float]] = []
            for segment in path:
                # Sample each primitive; the result is a polygonal approximation.
                try:
                    seg_len = float(segment.length(error=1e-3))
                except Exception:
                    seg_len = 1.0
                count = max(4, min(300, int(np.ceil(seg_len / 0.5)) + 1))
                for t in np.linspace(0.0, 1.0, count, endpoint=False):
                    z = segment.point(float(t))
                    xy = (float(z.real), -float(z.imag))  # SVG y-down -> CNC y-up
                    if not points or (abs(points[-1][0] - xy[0]) + abs(points[-1][1] - xy[1]) > 1e-7):
                        points.append(xy)
            if path and len(points) >= 3:
                end = path.point(1.0)
                start = path.point(0.0)
                if abs(end - start) <= 1e-3:
                    points.append(points[0])
                elif points:
                    # Polygonize can also join linework from separate SVG elements.
                    pass
                lines.append(LineString(points))
        return _build_polygons(lines)
    except Exception as exc:
        if isinstance(exc, (ValueError, RuntimeError)):
            raise
        raise ValueError(f"Could not read SVG: {exc}") from exc


def scale_geometry_mm(geometry, factor: float):
    if factor <= 0:
        raise ValueError("Scale factor must be greater than zero.")
    return scale_geometry(geometry, xfact=factor, yfact=factor, origin=(0, 0))


def load_mesh_for_preview(data: bytes, filename: str):
    """Return (vertices, faces, bounds) for a mesh preview; this does not generate 3D CAM."""
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError("Mesh preview needs trimesh. Run: pip install -r requirements.txt") from exc
    suffix = Path(filename).suffix.lower() or ".stl"
    try:
        loaded = trimesh.load(io.BytesIO(data), file_type=suffix.lstrip("."), force="mesh", process=False)
        if isinstance(loaded, trimesh.Scene):
            loaded = loaded.dump(concatenate=True)
        if not isinstance(loaded, trimesh.Trimesh) or len(loaded.vertices) == 0 or len(loaded.faces) == 0:
            raise ValueError("File did not contain a triangular mesh.")
        vertices = np.asarray(loaded.vertices, dtype=float)
        faces = np.asarray(loaded.faces, dtype=int)
        bounds = np.asarray(loaded.bounds, dtype=float)
        return vertices, faces, bounds
    except Exception as exc:
        raise ValueError(f"Could not load mesh file: {exc}") from exc


def geometry_to_dxf(geometry) -> bytes:
    """Export Shapely polygon/line geometry to a portable R2010 DXF."""
    try:
        import ezdxf
    except ImportError as exc:
        raise RuntimeError("DXF export needs ezdxf. Run: pip install -r requirements.txt") from exc
    doc = ezdxf.new("R2010")
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    geoms = list(geometry.geoms) if hasattr(geometry, "geoms") else [geometry]
    for geom in geoms:
        if geom.geom_type == "Polygon":
            msp.add_lwpolyline(list(geom.exterior.coords)[:-1], close=True, dxfattribs={"layer": "OUTLINE"})
            for ring in geom.interiors:
                msp.add_lwpolyline(list(ring.coords)[:-1], close=True, dxfattribs={"layer": "HOLES"})
        elif geom.geom_type in ("LineString", "LinearRing"):
            msp.add_lwpolyline(list(geom.coords), close=bool(geom.is_ring), dxfattribs={"layer": "LINEWORK"})
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def project_mesh_to_2d(data: bytes, filename: str, view: str = "Top (XY)"):
    """Project a triangulated mesh into an orthographic 2D footprint for drawing export.

    This is a projected silhouette/footprint approximation, not a hidden-line drawing.
    STEP import is attempted through the optional CadQuery/OCP stack when installed.
    """
    suffix = Path(filename).suffix.lower()
    if suffix == ".step" or suffix == ".stp":
        try:
            import cadquery as cq
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
                temp.write(data); step_path = temp.name
            try:
                shape = cq.importers.importStep(step_path)
                verts, tris = shape.val().tessellate(0.1, 0.1)
                import trimesh
                mesh = trimesh.Trimesh(vertices=np.asarray([[v.x, v.y, v.z] for v in verts]),
                                       faces=np.asarray([[t[0], t[1], t[2]] for t in tris]), process=False)
            finally:
                os.unlink(step_path)
            loaded = mesh
        except ImportError as exc:
            raise RuntimeError("STEP projection requires CadQuery. Install with `pip install cadquery`; alternatively export STEP to STL from FreeCAD and upload the STL.") from exc
        except Exception as exc:
            raise ValueError(f"Could not read STEP model: {exc}") from exc
        vertices = np.asarray(loaded.vertices, dtype=float)
        faces = np.asarray(loaded.faces, dtype=int)
    else:
        vertices, faces, _bounds = load_mesh_for_preview(data, filename)
    axes = {"Top (XY)": (0, 1), "Front (XZ)": (0, 2), "Side (YZ)": (1, 2)}
    ax0, ax1 = axes[view]
    # Union of projected triangles gives the orthographic projected footprint.
    from shapely.geometry import Polygon as ShapelyPolygon
    triangles = []
    for face in faces:
        pts = vertices[np.asarray(face, dtype=int)][:, [ax0, ax1]]
        poly = ShapelyPolygon(pts)
        if poly.is_valid and poly.area > 1e-10:
            triangles.append(poly)
    if not triangles:
        raise ValueError("Could not derive a 2D footprint from this model/view.")
    projected = unary_union(triangles)
    if projected.is_empty:
        raise ValueError("The projected model produced no usable 2D geometry.")
    return projected
