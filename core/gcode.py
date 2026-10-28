"""ISO-style metric G-code generation for 2.5D contouring and thread-milling demos."""
from __future__ import annotations

import math

import numpy as np
from dataclasses import dataclass
from typing import Sequence

Point2 = tuple[float, float]
Path2 = Sequence[Point2]


@dataclass
class MachiningConfig:
    tool_diameter: float = 4.0
    total_depth: float = 3.0
    depth_per_pass: float = 1.0
    feed_rate: float = 300.0
    plunge_feed: float = 100.0
    spindle_speed: int = 3000
    safe_z: float = 5.0
    rapid_rate: float = 1500.0
    stock_allowance: float = 0.0

    def validate(self):
        if self.tool_diameter <= 0: raise ValueError("Tool diameter must be greater than zero.")
        if self.total_depth <= 0: raise ValueError("Total depth must be greater than zero.")
        if self.depth_per_pass <= 0: raise ValueError("Depth per pass must be greater than zero.")
        if self.feed_rate <= 0 or self.plunge_feed <= 0 or self.rapid_rate <= 0:
            raise ValueError("Feed, plunge feed and rapid rate must be greater than zero.")
        if self.spindle_speed <= 0: raise ValueError("Spindle speed must be greater than zero.")
        if self.safe_z <= 0: raise ValueError("Safe Z must be positive.")
        if self.stock_allowance < 0: raise ValueError("Stock allowance cannot be negative.")


def fmt(value: float) -> str:
    if abs(value) < 0.0005:
        value = 0.0
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def _clean_path(path: Path2) -> list[Point2]:
    points = [(float(x), float(y)) for x, y in path]
    if len(points) < 2:
        raise ValueError("Each toolpath must contain at least two points.")
    if not all(math.isfinite(v) for point in points for v in point):
        raise ValueError("Geometry contains invalid coordinates.")
    # Closed Shapely rings already repeat their first coordinate at the end. Open
    # raster-pocket segments must remain open so the cutter does not cut an invented chord.
    return points


def _depth_levels(total_depth: float, depth_per_pass: float) -> list[float]:
    count = int(math.ceil(total_depth / depth_per_pass))
    return [min(total_depth, i * depth_per_pass) for i in range(1, count + 1)]


def _path_length(path: Path2) -> float:
    return sum(math.dist(path[i - 1], path[i]) for i in range(1, len(path)))


def estimate_contour_time(paths: list[list[Point2]], finish_paths: list[list[Point2]], cfg: MachiningConfig) -> dict:
    depths = _depth_levels(cfg.total_depth, cfg.depth_per_pass)
    cut_len = sum(_path_length(p) for p in paths) * len(depths)
    if finish_paths:
        cut_len += sum(_path_length(p) for p in finish_paths)
    path_count = len(paths) * len(depths) + len(finish_paths)
    cutting_seconds = cut_len / cfg.feed_rate * 60.0
    plunge_seconds = path_count * cfg.total_depth / cfg.plunge_feed * 60.0
    # Very rough XY rapid estimate: one approach per contour, plus a short retract allowance.
    rapid_distance = path_count * (cfg.safe_z + 10.0)
    rapid_seconds = rapid_distance / cfg.rapid_rate * 60.0
    return {
        "cut_length_mm": cut_len,
        "cutting_seconds": cutting_seconds,
        "estimated_seconds": cutting_seconds + plunge_seconds + rapid_seconds,
        "passes": len(depths),
        "path_count": path_count,
    }


def generate_contour_gcode(paths: list[Path2], cfg: MachiningConfig,
                           finish_paths: list[Path2] | None = None,
                           title: str = "2.5D contour") -> tuple[str, dict]:
    """Generate conservative, retract-between-contours metric G-code for 2.5D milling."""
    cfg.validate()
    finish_paths = finish_paths or []
    clean_rough = [_clean_path(p) for p in paths]
    clean_finish = [_clean_path(p) for p in finish_paths]
    if not clean_rough and not clean_finish:
        raise ValueError("No toolpaths are available for G-code generation.")
    depths = _depth_levels(cfg.total_depth, cfg.depth_per_pass)
    lines = [
        "%", f"(CIM prototype - {title})", "(Units: mm; work offset G54; absolute XY/Z)",
        "(SIMULATE AND VERIFY BEFORE ANY MACHINE USE)", "G21", "G90", "G17", "G94", "G54",
        f"G00 Z{fmt(cfg.safe_z)}", f"M03 S{cfg.spindle_speed}",
    ]

    def emit_path(path: list[Point2], depth: float):
        x0, y0 = path[0]
        lines.append(f"G00 Z{fmt(cfg.safe_z)}")
        lines.append(f"G00 X{fmt(x0)} Y{fmt(y0)}")
        lines.append(f"G01 Z-{fmt(depth)} F{fmt(cfg.plunge_feed)}")
        for x, y in path[1:]:
            lines.append(f"G01 X{fmt(x)} Y{fmt(y)} F{fmt(cfg.feed_rate)}")
        lines.append(f"G00 Z{fmt(cfg.safe_z)}")

    for depth in depths:
        lines.append(f"(Rough contour depth -{fmt(depth)} mm)")
        for path in clean_rough:
            emit_path(path, depth)
    if clean_finish:
        lines.append("(Finish contour at full depth)")
        for path in clean_finish:
            emit_path(path, cfg.total_depth)
    lines.extend(["M05", f"G00 Z{fmt(cfg.safe_z)}", "G00 X0 Y0", "M30", "%"])
    metrics = estimate_contour_time(clean_rough, clean_finish, cfg)
    return "\n".join(lines) + "\n", metrics


def generate_thread_gcode(major_diameter: float, tool_diameter: float, pitch: float,
                          thread_length: float, cfg: MachiningConfig,
                          direction: str = "G03", center_x: float = 0.0,
                          center_y: float = 0.0) -> tuple[str, list[tuple[float, float, float]], dict]:
    """Generate a simplified internal thread-milling helix using semicircular arcs.

    The path-radius formula follows the project's review-deck example, R=(D-d_tool)/2.
    This is an educational centreline model, not an ISO thread-profile/cutter compensation
    solver. Length is rounded up to a whole number of pitches so the arc pitch is consistent.
    """
    cfg.validate()
    if major_diameter <= 0 or tool_diameter <= 0 or pitch <= 0 or thread_length <= 0:
        raise ValueError("Diameter, tool diameter, pitch and thread length must be positive.")
    if tool_diameter >= major_diameter:
        raise ValueError("Thread mill diameter must be smaller than the nominal hole/thread diameter.")
    if direction not in {"G02", "G03"}:
        raise ValueError("Arc direction must be G02 or G03.")
    radius = (major_diameter - tool_diameter) / 2.0
    turns = max(1, math.ceil(thread_length / pitch))
    actual_length = turns * pitch
    sign = -1.0 if direction == "G03" else 1.0
    lines = [
        "%", "(CIM prototype - simplified internal thread milling)",
        "(Path radius = (nominal diameter - tool diameter)/2)",
        "(SIMULATE AND VERIFY THREAD PROFILE, HAND, CLEARANCE AND COMPENSATION)",
        "G21", "G90", "G17", "G94", "G54", f"G00 Z{fmt(cfg.safe_z)}",
        f"M03 S{cfg.spindle_speed}",
        f"G00 X{fmt(center_x + radius)} Y{fmt(center_y)}",
        f"G01 Z0 F{fmt(cfg.plunge_feed)}",
    ]
    xyz: list[tuple[float, float, float]] = [(center_x + radius, center_y, 0.0)]
    angular_sign = 1.0 if direction == "G03" else -1.0
    for half in range(1, 2 * turns + 1):
        z = -half * pitch / 2.0
        if half % 2 == 1:
            x_end = center_x - radius
            i = -radius
            j = 0.0
        else:
            x_end = center_x + radius
            i = radius
            j = 0.0
        # On each semicircle the endpoint is the opposite side of the circle.
        lines.append(f"{direction} X{fmt(x_end)} Y{fmt(center_y)} Z{fmt(z)} I{fmt(i)} J{fmt(j)} F{fmt(cfg.feed_rate)}")
        # Sample the helical arc for a smooth 3D visualization (the posted G-code still uses arcs).
        theta0 = (half - 1) * angular_sign * math.pi
        theta1 = half * angular_sign * math.pi
        z0 = -(half - 1) * pitch / 2.0
        for t in np.linspace(0.0, 1.0, 25)[1:]:
            theta = theta0 + (theta1 - theta0) * float(t)
            z_sample = z0 + (z - z0) * float(t)
            xyz.append((center_x + radius * math.cos(theta), center_y + radius * math.sin(theta), z_sample))
    lines.extend([f"G00 Z{fmt(cfg.safe_z)}", "M05", f"G00 X0 Y0", "M30", "%"])
    arc_len_per_half = math.pi * radius
    feed_seconds = (arc_len_per_half * 2 * turns) / cfg.feed_rate * 60.0
    feed_seconds = math.sqrt(feed_seconds**2 + (actual_length / cfg.feed_rate * 60.0)**2)
    metrics = {"path_radius_mm": radius, "turns": turns, "programmed_length_mm": actual_length,
               "estimated_seconds": feed_seconds, "cut_length_mm": arc_len_per_half * 2 * turns}
    return "\n".join(lines) + "\n", xyz, metrics


def basic_gcode_checks(code: str) -> list[str]:
    """Lightweight sanity checks; not a controller parser or machine safety certification."""
    issues: list[str] = []
    upper = code.upper()
    for required in ("G21", "G90", "G17", "M03", "M05", "M30"):
        if required not in upper:
            issues.append(f"Missing expected command {required}.")
    if "G00 Z" not in upper:
        issues.append("No explicit safe-Z rapid move was found.")
    if "NAN" in upper or "INF" in upper:
        issues.append("Output contains a non-finite number.")
    if not code.strip().endswith("%"):
        issues.append("Program end delimiter (%) is missing.")
    return issues
