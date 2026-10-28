from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import streamlit as st
from shapely.geometry import Polygon

from core.geometry import (
    approx_root_gap, blade_polygon, contour_toolpaths, gear_polygon,
    polygon_parts, pocket_toolpaths, translate_to_origin,
)
from core.importers import (load_dxf, load_mesh_for_preview, load_svg, scale_geometry_mm,
                            geometry_to_dxf, project_mesh_to_2d)
from core.gcode import MachiningConfig, basic_gcode_checks, generate_contour_gcode, generate_thread_gcode

st.set_page_config(page_title="CIM Studio | G-code Generator", page_icon="⚙️", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
:root { --brand: #2563eb; }
.block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1500px; }
[data-testid="stSidebar"] { border-right: 1px solid rgba(128,128,128,.22); }
.hero { padding: 1.35rem 1.5rem; border-radius: 16px; background: linear-gradient(120deg, rgba(37,99,235,.14), rgba(14,165,233,.06)); border: 1px solid rgba(100,116,139,.22); margin-bottom: 1rem; }
.hero-kicker { text-transform: uppercase; letter-spacing: .14em; font-size: .72rem; font-weight: 700; opacity: .75; }
.hero h1 { margin: .25rem 0 .3rem 0; font-size: 2rem; letter-spacing: -.035em; }
.hero p { margin: 0; opacity: .82; }
[data-testid="stMetric"] { background: rgba(128,128,128,.055); padding: .8rem 1rem; border: 1px solid rgba(128,128,128,.16); border-radius: 12px; }
.stButton>button, .stDownloadButton>button { border-radius: 9px; font-weight: 600; }
[data-testid="stFileUploader"] { border: 1px dashed rgba(100,116,139,.55); border-radius: 12px; padding: .35rem; }
hr { opacity: .35; }
</style>
<div class="hero"><div class="hero-kicker">Computer Integrated Manufacturing · Engineering workspace</div><h1>CIM Studio</h1><p>Parametric geometry · CAD import and conversion · toolpath preview · ISO-style G-code</p></div>
""", unsafe_allow_html=True)

st.warning(
    "Prototype / simulation use only. This app is not a certified CAM system. Always verify the setup, "
    "tool, work offset, fixtures, clearances, controller dialect and generated code in a CNC simulator "
    "before any machine use. Never run generated code directly on a machine without qualified review."
)

MODE_GEAR = "Parametric spur gear"
MODE_THREAD = "Thread milling (helical)"
MODE_BLADE = "Simplified blade / airfoil contour"
MODE_FILE = "Import 2D geometry (DXF / SVG)"
MODE_MESH = "3D model → 2D drawing (STL / OBJ / PLY / STEP)"
MODE_CONVERT = "SVG → DXF converter"
mode = st.sidebar.selectbox("Workspace", [MODE_GEAR, MODE_THREAD, MODE_BLADE, MODE_FILE, MODE_CONVERT, MODE_MESH])

st.sidebar.markdown("### Setup")
st.sidebar.caption("Units: millimetres · metric ISO-style output")
st.sidebar.header("Machining parameters")
def_tool = 5.0 if mode == MODE_THREAD else (2.0 if mode == MODE_GEAR else 4.0)
tool_diameter = st.sidebar.number_input("Tool diameter (mm)", min_value=0.1, max_value=100.0, value=float(def_tool), step=0.5, key=f"tool_{mode}")
total_depth = st.sidebar.number_input("Total depth (mm)", min_value=0.1, max_value=100.0, value=3.0, step=0.5)
depth_per_pass = st.sidebar.number_input("Depth per pass (mm)", min_value=0.1, max_value=20.0, value=1.0, step=0.5)
feed_rate = st.sidebar.number_input("Cutting feed (mm/min)", min_value=1.0, max_value=20000.0, value=300.0, step=25.0)
plunge_feed = st.sidebar.number_input("Plunge feed (mm/min)", min_value=1.0, max_value=5000.0, value=100.0, step=10.0)
spindle_speed = st.sidebar.number_input("Spindle speed (RPM)", min_value=1, max_value=60000, value=3000, step=100)
safe_z = st.sidebar.number_input("Safe Z height (mm)", min_value=0.5, max_value=100.0, value=5.0, step=0.5)
rapid_rate = st.sidebar.number_input("Estimated rapid rate (mm/min)", min_value=1.0, max_value=30000.0, value=1500.0, step=100.0)
stock_allowance = st.sidebar.number_input("Roughing stock allowance (mm)", min_value=0.0, max_value=10.0, value=0.2, step=0.1)

cfg = MachiningConfig(tool_diameter=float(tool_diameter), total_depth=float(total_depth),
                      depth_per_pass=float(depth_per_pass), feed_rate=float(feed_rate),
                      plunge_feed=float(plunge_feed), spindle_speed=int(spindle_speed),
                      safe_z=float(safe_z), rapid_rate=float(rapid_rate),
                      stock_allowance=float(stock_allowance))


def add_polygon_trace(fig: go.Figure, geom, name: str, color: str, width: int = 2, fill: bool = False):
    for idx, poly in enumerate(polygon_parts(geom)):
        x, y = zip(*list(poly.exterior.coords))
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=name if idx == 0 else f"{name} {idx+1}",
                                 line=dict(color=color, width=width), fill="toself" if fill else None,
                                 opacity=0.22 if fill else 1.0))
        for hole in poly.interiors:
            hx, hy = zip(*list(hole.coords))
            fig.add_trace(go.Scatter(x=hx, y=hy, mode="lines", showlegend=False,
                                     line=dict(color=color, width=width, dash="dot")))


def add_paths_trace(fig: go.Figure, paths, name: str, color: str, width: int = 1):
    first = True
    for path in paths:
        if not path:
            continue
        x, y = zip(*path)
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=name if first else None,
                                 showlegend=first, line=dict(color=color, width=width)))
        first = False


def configure_2d(fig: go.Figure, title: str = "Geometry and generated toolpath"):
    fig.update_layout(title=title, height=610, margin=dict(l=10, r=10, t=50, b=10),
                      xaxis_title="X (mm)", yaxis_title="Y (mm)", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                      hovermode="closest")
    fig.update_yaxes(scaleanchor="x", scaleratio=1)
    return fig


def output_code(code: str, metrics: dict, rough_paths=None, finish_paths=None,
                profile=None, title="Toolpath preview", extra_message: str | None = None):
    if extra_message:
        st.info(extra_message)
    checks = basic_gcode_checks(code)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Toolpath / cut length", f"{metrics.get('cut_length_mm', 0):.1f} mm")
    col2.metric("Estimated time", f"{metrics.get('estimated_seconds', 0)/60:.1f} min")
    col3.metric("Depth passes / turns", str(metrics.get('passes', metrics.get('turns', '—'))))
    col4.metric("Toolpath paths", str(metrics.get('path_count', len(rough_paths or []))))

    st.subheader("Geometry and toolpath preview")
    fig = go.Figure()
    if profile is not None:
        add_polygon_trace(fig, profile, "Part outline", "#2563eb", 2)
    if rough_paths:
        add_paths_trace(fig, rough_paths, "Rough / raster path", "#ea580c", 1)
    if finish_paths:
        add_paths_trace(fig, finish_paths, "Finish path", "#16a34a", 2)
    configure_2d(fig, title)
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Generated G-code")
    if checks:
        st.error("Basic sanity checks flagged: " + " ".join(checks))
    else:
        st.success("Basic text-level checks passed. This is not a complete G-code parser or safety validation.")
    st.download_button("⬇️ Download G-code (.nc)", data=code, file_name="generated_part.nc", mime="text/plain", type="primary")
    with st.expander("View G-code text"):
        st.code(code, language="text", line_numbers=True)
    st.caption("Time estimate is approximate and excludes setup, acceleration/deceleration, tool changes and controller-specific behavior.")


if mode == MODE_GEAR:
    st.header("1. Parametric spur gear")
    a, b, c = st.columns(3)
    module = a.number_input("Gear module (mm)", min_value=0.25, max_value=20.0, value=3.0, step=0.25)
    teeth = b.number_input("Number of teeth", min_value=8, max_value=150, value=20, step=1)
    pressure_angle = c.number_input("Pressure angle (degrees)", min_value=10.0, max_value=35.0, value=20.0, step=1.0)
    pitch_diameter = module * teeth
    base_diameter = pitch_diameter * math.cos(math.radians(pressure_angle))
    outer_diameter = module * (teeth + 2)
    x1, x2, x3 = st.columns(3)
    x1.metric("Pitch diameter", f"{pitch_diameter:.2f} mm")
    x2.metric("Base diameter", f"{base_diameter:.2f} mm")
    x3.metric("Addendum diameter", f"{outer_diameter:.2f} mm")
    try:
        profile = gear_polygon(float(module), int(teeth), float(pressure_angle))
        rough, finish = contour_toolpaths(profile, cfg.tool_diameter, "outside", cfg.stock_allowance)
        gap = approx_root_gap(float(module), int(teeth), float(pressure_angle))
        msg = None
        if cfg.tool_diameter > gap:
            msg = (f"Tool-fit warning: the simplified profile estimates the root opening at about {gap:.2f} mm, "
                   f"smaller than the selected {cfg.tool_diameter:.2f} mm cutter. The path can be previewed, "
                   "but it may not reproduce the tooth spaces. Choose a smaller tool / larger module and verify in CAM.")
            st.warning(msg)
        code, metrics = generate_contour_gcode(rough, cfg, finish, title="parametric external spur gear")
        output_code(code, metrics, rough, finish, profile, "Spur gear · outside tool-radius compensation", msg)
        st.caption("Geometry model: analytically sampled involute flanks with simplified root transitions. It is not a production gear-design standard or an undercut/root-fillet solver.")
    except Exception as exc:
        st.error(str(exc))

elif mode == MODE_THREAD:
    st.header("2. Thread milling using helical interpolation")
    a, b, c = st.columns(3)
    major_d = a.number_input("Nominal thread / hole diameter (mm)", min_value=1.0, max_value=200.0, value=10.0, step=0.5)
    pitch = b.number_input("Thread pitch (mm)", min_value=0.1, max_value=20.0, value=1.0, step=0.1)
    thread_len = c.number_input("Thread length (mm)", min_value=0.1, max_value=200.0, value=8.0, step=0.5)
    direction = st.selectbox("Helical arc direction", ["G03", "G02"], help="Arc direction depends on thread hand, internal/external cutting, and controller setup. Verify before use.")
    center_x = st.number_input("Thread centre X (mm)", value=0.0, step=1.0)
    center_y = st.number_input("Thread centre Y (mm)", value=0.0, step=1.0)
    try:
        code, xyz, metrics = generate_thread_gcode(float(major_d), cfg.tool_diameter, float(pitch), float(thread_len), cfg, direction, float(center_x), float(center_y))
        col1, col2, col3 = st.columns(3)
        col1.metric("Toolpath radius", f"{metrics['path_radius_mm']:.3f} mm")
        col2.metric("Number of revolutions", str(metrics['turns']))
        col3.metric("Programmed axial span", f"{metrics['programmed_length_mm']:.3f} mm")
        st.info("This uses the review-deck centreline example R = (nominal diameter − tool diameter)/2 and advances Z by one pitch per revolution. Thread profile, hand, cutter compensation and lead-in/out are simplified; validate the actual thread dimensions separately.")
        st.subheader("Helical path preview")
        arr = np.asarray(xyz, dtype=float)
        fig = go.Figure(data=[go.Scatter3d(x=arr[:, 0], y=arr[:, 1], z=arr[:, 2], mode="lines+markers",
                                             marker=dict(size=2), line=dict(width=5), name="Helical tool-centre path")])
        fig.update_layout(height=570, scene=dict(xaxis_title="X (mm)", yaxis_title="Y (mm)", zaxis_title="Z (mm)", aspectmode="data"), margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)
        st.subheader("Generated G-code")
        checks = basic_gcode_checks(code)
        if checks: st.error("Basic sanity checks flagged: " + " ".join(checks))
        else: st.success("Basic text-level checks passed; this does not validate thread geometry or machine safety.")
        st.download_button("⬇️ Download G-code (.nc)", data=code, file_name="thread_milling.nc", mime="text/plain", type="primary")
        with st.expander("View G-code text"): st.code(code, language="text", line_numbers=True)
        st.caption(f"Estimated cutting-only time: {metrics['estimated_seconds']:.1f} s; machine acceleration, lead-in and retract timing are excluded.")
    except Exception as exc:
        st.error(str(exc))

elif mode == MODE_BLADE:
    st.header("3. Simplified blade / freeform contour")
    a, b = st.columns(2)
    chord = a.number_input("Chord length (mm)", min_value=5.0, max_value=500.0, value=60.0, step=5.0)
    thickness = b.number_input("Thickness ratio", min_value=0.04, max_value=0.30, value=0.12, step=0.01,
                               help="0.12 represents a 12% maximum thickness ratio.")
    try:
        profile = blade_polygon(float(chord), float(thickness))
        rough, finish = contour_toolpaths(profile, cfg.tool_diameter, "outside", cfg.stock_allowance)
        code, metrics = generate_contour_gcode(rough, cfg, finish, title="simplified 2D blade contour")
        output_code(code, metrics, rough, finish, profile, "Simplified airfoil · 2.5D outer contour")
        st.caption("This module is a 2D airfoil-like outline only. It is not a turbine blade surface model and does not generate 5-axis tool orientation, gouge avoidance or collision-free sculptured-surface paths.")
    except Exception as exc:
        st.error(str(exc))

elif mode == MODE_FILE:
    st.header("4. Import a 2D CAD contour")
    st.write("Import closed DXF or SVG contours. DXF block inserts are expanded where possible; curves are sampled for toolpath calculation. For SVG, use 1 drawing unit = 1 mm.")
    uploaded = st.file_uploader("Choose a .dxf or .svg file", type=["dxf", "svg"])
    a, b = st.columns(2)
    scale = a.number_input("Geometry scale factor", min_value=0.001, max_value=1000.0, value=1.0, step=0.1, help="Use 1.0 when imported coordinates are already millimetres.")
    strategy = b.selectbox("Machining strategy", ["Outside contour", "Inside pocket (raster)"])
    step_over = st.number_input("Pocket step-over (mm)", min_value=0.1, max_value=100.0, value=min(2.0, max(0.1, tool_diameter * 0.5)), step=0.5, help="Used for raster pocket strategy; cannot exceed tool diameter.")
    if uploaded:
        try:
            data = uploaded.getvalue()
            geometry = load_dxf(data) if uploaded.name.lower().endswith(".dxf") else load_svg(data)
            geometry = scale_geometry_mm(geometry, float(scale))
            # For this UI the largest polygon is treated as the primary part/pocket boundary;
            # additional loops are retained if import polygonization returned multiple components.
            parts = polygon_parts(geometry)
            if not parts:
                raise ValueError("No valid closed polygons were found.")
            profile = geometry
            minx, miny, maxx, maxy = profile.bounds
            st.info(f"Imported {len(parts)} closed polygon(s) · bounding box {(maxx-minx):.2f} × {(maxy-miny):.2f} mm · area {sum(p.area for p in parts):.2f} mm²")
            if strategy == "Outside contour":
                rough, finish = contour_toolpaths(profile, cfg.tool_diameter, "outside", cfg.stock_allowance)
            else:
                rough, finish = pocket_toolpaths(profile, cfg.tool_diameter, float(step_over), cfg.stock_allowance)
            code, metrics = generate_contour_gcode(rough, cfg, finish, title=f"imported {Path(uploaded.name).suffix[1:].upper()} geometry")
            output_code(code, metrics, rough, finish, profile, f"Imported geometry · {strategy}")
            st.caption("Importer uses closed loops only. Open/incomplete contours, cutter compensation for nested islands, fixtures, stock removal simulation and machine-specific postprocessing are not implemented.")
        except Exception as exc:
            st.error(f"Import/toolpath error: {exc}")
            with st.expander("Troubleshoot this CAD file"):
                st.markdown("""
                - Open the file in your CAD tool and switch to **Model space**.
                - Select only the intended part outline; explode block references if the import still reports no geometry.
                - Convert text, dimensions and hatch patterns to curves only if they are intended as geometry.
                - Join endpoints and close every contour. For a 3D part, use the **3D model → 2D drawing** workspace below.
                - Export as **DXF R12 or DXF 2010** and confirm the drawing units. Use the scale factor above if necessary.
                """)
    else:
        sample_path = Path(__file__).parent / "examples" / "sample_contour.svg"
        st.info("No file uploaded yet. You can try the bundled example: " + str(sample_path.name))
        try:
            geometry = load_svg(sample_path.read_bytes())
            fig = go.Figure()
            add_polygon_trace(fig, geometry, "Bundled example SVG", "#2563eb", 2)
            configure_2d(fig, "Bundled example contour (preview only)")
            st.plotly_chart(fig, use_container_width=True)
        except Exception:
            pass

elif mode == MODE_CONVERT:
    st.header("5. SVG → DXF converter")
    st.write("Convert closed SVG paths into a millimetre-based DXF with editable polylines. This is useful for laser/CAD artwork and as a bridge into the 2D contour workflow.")
    svg_file = st.file_uploader("Choose an SVG file", type=["svg"], key="svg_to_dxf")
    ca, cb = st.columns([1, 2])
    svg_scale = ca.number_input("SVG scale factor", min_value=0.001, max_value=1000.0, value=1.0, step=0.1, help="Scale applied to SVG coordinates before exporting. Use 1.0 when one SVG unit represents one mm.")
    ca.caption("DXF units are set to millimetres.")
    if svg_file:
        try:
            converted = scale_geometry_mm(load_svg(svg_file.getvalue()), float(svg_scale))
            minx, miny, maxx, maxy = converted.bounds
            cb.metric("Drawing extents", f"{maxx-minx:.2f} × {maxy-miny:.2f} mm")
            fig = go.Figure()
            add_polygon_trace(fig, converted, "Converted outline", "#2563eb", 2, fill=True)
            configure_2d(fig, "SVG geometry preview")
            st.plotly_chart(fig, use_container_width=True)
            dxf_bytes = geometry_to_dxf(converted)
            st.success("Conversion ready. The output contains closed outline polylines and preserved polygonal holes.")
            st.download_button("Download converted DXF", data=dxf_bytes, file_name=f"{Path(svg_file.name).stem}.dxf", mime="application/dxf", type="primary")
            st.caption("SVG transforms, CSS styles and physical units embedded in viewBox are not fully interpreted. Check the preview and dimensions before using the DXF for machining.")
        except Exception as exc:
            st.error(f"SVG conversion error: {exc}")
    else:
        st.info("Upload an SVG to preview and convert it. The bundled examples/sample_contour.svg is a good first test.")

elif mode == MODE_MESH:
    st.header("6. 3D model → 2D orthographic drawing")
    st.write("Create a projected 2D footprint from STL/OBJ/PLY meshes, or from STEP if the optional CadQuery package is installed. Export the result as DXF, then inspect it in the 2D import workspace before considering any toolpath.")
    mesh_file = st.file_uploader("Choose a 3D model", type=["stl", "obj", "ply", "step", "stp"], key="mesh_projection")
    view = st.selectbox("Projection plane", ["Top (XY)", "Front (XZ)", "Side (YZ)"])
    if mesh_file:
        try:
            projected = project_mesh_to_2d(mesh_file.getvalue(), mesh_file.name, view)
            minx, miny, maxx, maxy = projected.bounds
            a, b, c = st.columns(3)
            a.metric("Projected width", f"{maxx-minx:.2f} model units")
            b.metric("Projected height", f"{maxy-miny:.2f} model units")
            c.metric("Projection", view)
            fig = go.Figure()
            add_polygon_trace(fig, projected, "Projected footprint", "#2563eb", 2, fill=True)
            configure_2d(fig, f"Orthographic projection · {view}")
            st.plotly_chart(fig, use_container_width=True)
            st.download_button("Download 2D projection as DXF", data=geometry_to_dxf(projected),
                               file_name=f"{Path(mesh_file.name).stem}_{view.split()[0].lower()}.dxf",
                               mime="application/dxf", type="primary")
            st.warning("This is a projected footprint, not a full engineering drawing: hidden lines, dimensions, tolerances, section views and thread annotations are not generated. A projected footprint can merge separate surfaces. Inspect the exported DXF before machining.")
        except Exception as exc:
            st.error(f"Projection error: {exc}")
            if Path(mesh_file.name).suffix.lower() in (".step", ".stp"):
                st.info("For STEP support, install CadQuery in this environment (`pip install cadquery`) or export the STEP model to STL using FreeCAD first.")
    else:
        st.info("STL/OBJ/PLY work with the included mesh reader. STEP support is optional because its CAD kernel is substantially larger than the base application.")

st.divider()
with st.expander("How this prototype maps to the project review"):
    st.markdown("""
    - **Parametric modules:** involute-flank spur gear, helical thread-milling example, and simplified 2D blade contour.
    - **Machining inputs:** tool diameter, spindle speed, feed/plunge feed, depth of cut, safe Z, stock allowance and pocket step-over.
    - **Built-in path planning:** tool-radius offsets, depth passes, raster pocket clearing, explicit retracts/rapids and a rough time estimate.
    - **Validation/preview:** geometry/toolpath plots, tool-size warning for gear root clearance and basic text-level G-code checks.
    - **First dialect:** generic metric ISO-style G-code with G21/G90/G17/G94/G54, G00/G01 and G02/G03. Controller details vary.

    Current limitations are intentional: this is a demonstrator for 2.5D contour machining, not a replacement for a commercial CAM system. A generated path is a candidate program that still needs backplot, stock simulation, tool/fixture clearance review and controller-specific verification.
    """)
