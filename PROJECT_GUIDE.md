# Project guide: Automated Parametric G-code Generator

This is the fastest defensible implementation path for the project described in the abstract and literature-review deck: bring parametric geometry, machining inputs, visible toolpaths, basic checks and one G-code dialect into one small Python app.

## 1. System pipeline

```text
Parametric inputs or DXF/SVG file
              |
              v
   Geometry generation/import
              |
              v
  Validate closed, usable geometry
              |
              v
 Cutter-radius offset / pocket raster
              |
              v
       Depth-pass planner
              |
              v
   Toolpath and geometry preview
              |
              v
   Metric ISO-style G-code writer
              |
              v
 Text checks + time estimate + .nc export
```

Each stage is deliberately separate in code so the geometry and postprocessor can be tested without opening the UI.

## 2. What each module does

| File | Responsibility |
|---|---|
| `app.py` | Streamlit controls, plots, results, warnings and download buttons. |
| `core/geometry.py` | Spur-gear involute sampling, simplified NACA-style blade, Shapely cutter offsets and scanline pocket toolpaths. |
| `core/importers.py` | DXF/SVG contour extraction plus STL/OBJ/PLY mesh loading for visualization. |
| `core/gcode.py` | Depth scheduling, G00/G01 contour output, G02/G03 helical thread output, rough timing and basic text checks. |
| `tests/test_core.py` | Small regression tests for the core algorithms. |

## 3. Geometry and machining logic

### Spur gear

The app calculates:

- Pitch diameter: `d = module × tooth_count`
- Base diameter: `db = module × tooth_count × cos(pressure_angle)`
- Addendum diameter for the standard demonstrator: `da = module × (tooth_count + 2)`
- Involute angle: `inv(a) = tan(a) - a`

It samples involute flanks between the base and addendum circles. Root transitions are simplified. The nominal profile is buffered outward by half the cutter diameter so the cutter centre follows a candidate outer contour. A smaller cutter may be needed to reach tooth spaces; the app flags a rough root-clearance mismatch.

### Thread milling

The review deck's worked example is demonstrated directly:

- Nominal diameter = 10 mm
- Thread-mill diameter = 5 mm
- Pitch = 1 mm
- Helical path radius = `(10 - 5) / 2 = 2.5 mm`
- Z changes by half a pitch on each semicircular G02/G03 move, so one revolution advances one pitch.

The prototype rounds the requested axial length up to a whole number of pitches. It demonstrates the interpolation concept; it is not a full ISO thread-profile solver and does not automatically determine thread hand, minor diameter, cutter engagement, lead-in/out or machine-specific compensation.

### Simplified blade

The blade module builds a 2D symmetric airfoil-like profile from chord length and thickness ratio. It then uses the same outer-profile toolpath pipeline as the gear. It is not a 3D turbine blade surface or a five-axis strategy.

### DXF/SVG profile and pocket paths

Closed 2D outlines are flattened into sampled linework. The outside contour uses an outward buffer. Pocket clearing offsets inward by the tool radius, then clips horizontal scan lines to the inset pocket area. A step-over sets the spacing between scan lines. Open or incomplete contours are rejected instead of guessed closed.

### Depth planning and code

For total depth `D` and depth per pass `a`, the planner creates depths `min(a, D)`, `min(2a, D)`, … until `D` is reached. Each contour has a safe-Z retract before rapid XY positioning. The output uses a generic metric ISO-style sequence (`G21`, `G90`, `G17`, `G94`, `G54`, `G00`, `G01`, `M03`, `M05`, `M30`). Thread milling uses helical G02/G03 arcs with I/J centre offsets.

The time estimate is a rough feed-distance estimate. It omits acceleration, deceleration, controller look-ahead, setup, tool changes, material-dependent cutting limits and detailed rapid trajectory timing.

## 4. How to demonstrate the prototype in 3–5 minutes

1. Open the app and select **Parametric spur gear**. Change module or tooth count and point out that the profile updates, with pitch/base/addendum dimensions shown.
2. Change tool diameter and depth-per-pass. Explain why cutter size and depth scheduling are explicit inputs, and show the root-gap warning when the cutter is too large.
3. Select **Thread milling**. Set nominal diameter 10, tool diameter 5 and pitch 1. Show the 2.5 mm radius, helical preview and compact G02/G03 output.
4. Select **Import 2D geometry** and upload `examples/sample_contour.dxf`. Toggle **Outside contour** and **Inside pocket (raster)**, then vary step-over.
5. Download the `.nc` output and explain the difference between a basic text-level check and full controller/simulation verification.
6. Optionally upload a mesh under **Preview 3D mesh** to show CAD visualisation while being clear that mesh-to-3D-CAM is a future extension.

## 5. Fastest development sequence if time is very limited

- First, run the app and demonstrate the parametric gear/thread modules.
- Second, demonstrate DXF import and pocket/contour output.
- Third, prepare a short screen recording or screenshots: input parameters, geometry, toolpath, G-code and checks.
- Fourth, explain limitations candidly in the report: simplified root fillets, no controller-specific postprocessor, no stock-removal simulator, no arbitrary 3D mesh toolpath, and no physical machine validation.

## 6. What must be added before real machining

This application creates candidate programs for coursework demonstration, not verified production programs. A real system needs machine and controller profiles, stock/fixture/tool-holder models, tool-length/work offsets, material/tool cutting data, lead-in/ramp strategy, full arc syntax and modal-state parsing, stock-removal simulation, gouge/collision checks, and a trusted backplot. Arbitrary STL/OBJ machining additionally needs slice generation or a 3D toolpath strategy, not just a mesh viewer.

**Do not run output directly on a CNC machine.** First check the units, tool and work offsets, stock bounds, clearances, controller syntax and all Z values; then simulate and conduct a qualified dry run.
