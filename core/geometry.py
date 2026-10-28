"""Parametric geometry and 2D toolpath helpers. All linear units are millimetres."""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import unary_union


def involute_angle(alpha: float) -> float:
    return math.tan(alpha) - alpha


def gear_polygon(module: float, teeth: int, pressure_angle_deg: float = 20.0,
                 flank_samples: int = 18, arc_samples: int = 8) -> Polygon:
    """Create an approximate, closed external spur-gear outline from involute flanks.

    The involute flanks are analytically sampled. Root transition/root fillets are
    simplified, so this is appropriate for a demonstrator, not production gear cutting.
    """
    if module <= 0:
        raise ValueError("Gear module must be greater than zero.")
    if not isinstance(teeth, int) or teeth < 8:
        raise ValueError("Use at least 8 teeth for this prototype gear generator.")
    if not 10 <= pressure_angle_deg <= 35:
        raise ValueError("Pressure angle must be between 10 and 35 degrees.")

    z = teeth
    alpha = math.radians(pressure_angle_deg)
    pitch_r = module * z / 2.0
    base_r = pitch_r * math.cos(alpha)
    tip_r = pitch_r + module
    root_r = max(module * 0.2, pitch_r - 1.25 * module)
    inv_pitch = involute_angle(alpha)
    pitch_angle = 2.0 * math.pi / z

    def half_width(radius: float) -> float:
        if radius <= base_r:
            return math.pi / (2.0 * z) + inv_pitch
        local_alpha = math.acos(max(-1.0, min(1.0, base_r / radius)))
        return math.pi / (2.0 * z) + inv_pitch - involute_angle(local_alpha)

    half_base = half_width(base_r)
    half_tip = half_width(tip_r)
    # A simple widening transition below the base circle; root fillet is intentionally
    # approximated rather than represented as an involute/fillet CAD feature.
    half_root = min(pitch_angle * 0.47, half_base + 0.025 * pitch_angle)

    points: list[tuple[float, float]] = []
    for tooth in range(z):
        center = tooth * pitch_angle
        # Root-to-base transition on the left side.
        for radius, angle in ((root_r, center - half_root), (base_r, center - half_base)):
            points.append((radius * math.cos(angle), radius * math.sin(angle)))

        # Left involute flank: from base circle to addendum circle.
        for radius in np.linspace(base_r, tip_r, flank_samples):
            angle = center - half_width(float(radius))
            points.append((float(radius) * math.cos(angle), float(radius) * math.sin(angle)))

        # Addendum arc across the tooth tip.
        for angle in np.linspace(center - half_tip, center + half_tip, arc_samples):
            points.append((tip_r * math.cos(float(angle)), tip_r * math.sin(float(angle))))

        # Right flank travels from the tip back to the base circle.
        for radius in np.linspace(tip_r, base_r, flank_samples):
            angle = center + half_width(float(radius))
            points.append((float(radius) * math.cos(angle), float(radius) * math.sin(angle)))

        # Right base-to-root transition and the root arc to the next tooth.
        points.append((root_r * math.cos(center + half_root), root_r * math.sin(center + half_root)))
        next_start = center + pitch_angle - half_root
        for angle in np.linspace(center + half_root, next_start, arc_samples):
            points.append((root_r * math.cos(float(angle)), root_r * math.sin(float(angle))))

    poly = Polygon(points)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty or poly.geom_type != "Polygon" or poly.area <= 0:
        raise ValueError("Could not build a valid gear outline. Try changing module or tooth count.")
    return poly


def blade_polygon(chord: float = 60.0, thickness_ratio: float = 0.12,
                  samples: int = 100) -> Polygon:
    """Generate a simplified symmetric NACA-style 00xx airfoil outline in XY."""
    if chord <= 0:
        raise ValueError("Chord must be greater than zero.")
    if not 0.04 <= thickness_ratio <= 0.3:
        raise ValueError("Thickness ratio must be between 0.04 and 0.30.")
    x = np.linspace(0.0, 1.0, max(30, samples))
    t = thickness_ratio
    # Closed trailing edge coefficient (-0.1036) avoids a small open gap at x=chord.
    yt = 5.0 * t * (0.2969 * np.sqrt(np.maximum(x, 0)) - 0.1260 * x
                    - 0.3516 * x**2 + 0.2843 * x**3 - 0.1036 * x**4)
    upper = [(float(chord * xi), float(chord * yi)) for xi, yi in zip(x, yt)]
    lower = [(float(chord * xi), float(-chord * yi)) for xi, yi in zip(x[::-1], yt[::-1])]
    poly = Polygon(upper + lower)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty or poly.geom_type != "Polygon":
        raise ValueError("Could not generate the simplified blade contour.")
    return poly


def polygon_parts(geometry) -> list[Polygon]:
    """Return non-empty polygon components from a Shapely geometry."""
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return [g for g in geometry.geoms if not g.is_empty and g.area > 1e-8]
    if isinstance(geometry, GeometryCollection):
        result: list[Polygon] = []
        for g in geometry.geoms:
            result.extend(polygon_parts(g))
        return result
    return []


def closed_ring_paths(geometry, include_holes: bool = False) -> list[list[tuple[float, float]]]:
    paths: list[list[tuple[float, float]]] = []
    for polygon in polygon_parts(geometry):
        ring = list(polygon.exterior.coords)
        if len(ring) >= 4:
            paths.append([(float(x), float(y)) for x, y in ring])
        if include_holes:
            for interior in polygon.interiors:
                hole = list(interior.coords)
                if len(hole) >= 4:
                    paths.append([(float(x), float(y)) for x, y in hole])
    return paths


def contour_toolpaths(part: Polygon, tool_diameter: float, side: str = "outside",
                      stock_allowance: float = 0.0) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
    """Return (rough paths, finish paths) for a boundary contour.

    outside: tool centre is tool radius outside the part boundary.
    inside: tool centre is tool radius inside the supplied boundary (e.g. a pocket wall).
    """
    if tool_diameter <= 0:
        raise ValueError("Tool diameter must be greater than zero.")
    if stock_allowance < 0:
        raise ValueError("Stock allowance cannot be negative.")
    radius = tool_diameter / 2.0
    sign = 1.0 if side == "outside" else -1.0
    rough_geom = part.buffer(sign * (radius + stock_allowance), join_style="round", quad_segs=8)
    finish_geom = part.buffer(sign * radius, join_style="round", quad_segs=8)
    rough = closed_ring_paths(rough_geom, include_holes=True)
    finish = closed_ring_paths(finish_geom, include_holes=True) if stock_allowance > 1e-9 else []
    if not rough:
        raise ValueError("Tool offset removed the geometry. Reduce tool diameter or check the contour.")
    if stock_allowance > 1e-9 and not finish:
        raise ValueError("Finishing offset removed the geometry. Reduce tool diameter.")
    return rough, finish


def _line_parts(geometry) -> list[LineString]:
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, LineString):
        return [geometry] if geometry.length > 1e-8 else []
    if isinstance(geometry, (MultiLineString, GeometryCollection)):
        lines: list[LineString] = []
        for item in geometry.geoms:
            lines.extend(_line_parts(item))
        return lines
    return []


def pocket_toolpaths(part: Polygon, tool_diameter: float, step_over: float,
                     stock_allowance: float = 0.0) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
    """Create conservative raster pocket paths plus a perimeter finish path.

    The cutter is kept inside the supplied pocket boundary. Each raster segment is
    approached/retracted separately, favoring a simple safe prototype over speed.
    """
    if tool_diameter <= 0 or step_over <= 0:
        raise ValueError("Tool diameter and step-over must be greater than zero.")
    if step_over > tool_diameter:
        raise ValueError("For this prototype, step-over must not exceed tool diameter.")
    radius = tool_diameter / 2.0
    inset_rough = part.buffer(-(radius + stock_allowance), join_style="round", quad_segs=8)
    inset_finish = part.buffer(-radius, join_style="round", quad_segs=8)
    area = unary_union(polygon_parts(inset_rough))
    if area.is_empty:
        raise ValueError("Pocket is too small for the selected tool and allowance.")

    minx, miny, maxx, maxy = area.bounds
    paths: list[list[tuple[float, float]]] = []
    ys = np.arange(miny + step_over / 2.0, maxy + step_over / 2.0, step_over)
    flip = False
    span = maxx - minx + 2.0
    for y in ys:
        scan = LineString([(minx - span, float(y)), (maxx + span, float(y))])
        segments = sorted(_line_parts(area.intersection(scan)), key=lambda line: line.bounds[0])
        if flip:
            segments.reverse()
        for seg in segments:
            coords = [(float(x), float(yy)) for x, yy in seg.coords]
            if flip:
                coords.reverse()
            if len(coords) >= 2:
                paths.append(coords)
        flip = not flip

    # The roughing raster leaves a perimeter for a final contour at the deepest Z.
    finish = closed_ring_paths(inset_finish, include_holes=True)
    if not paths and not finish:
        raise ValueError("No valid pocket paths were produced; check the contour and step-over.")
    return paths, finish


def translate_to_origin(geometry):
    """Move the lower-left bounding-box corner to (0, 0), preserving shape."""
    minx, miny, _, _ = geometry.bounds
    return affinity.translate(geometry, xoff=-minx, yoff=-miny)


def approx_root_gap(module: float, teeth: int, pressure_angle_deg: float = 20.0) -> float:
    """Rough estimate of space width at the root of the generated demonstrator gear."""
    z = teeth
    pitch_r = module * z / 2.0
    root_r = max(module * 0.2, pitch_r - 1.25 * module)
    alpha = math.radians(pressure_angle_deg)
    base_r = pitch_r * math.cos(alpha)
    half_base = math.pi / (2.0 * z) + involute_angle(alpha)
    half_root = min((2.0 * math.pi / z) * 0.47, half_base + 0.025 * (2.0 * math.pi / z))
    gap_angle = max(0.0, 2.0 * math.pi / z - 2.0 * half_root)
    return 2.0 * root_r * math.sin(gap_angle / 2.0)
