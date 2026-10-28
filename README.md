# Automated Parametric G-code Generator (CIM prototype)

A Python/Streamlit prototype aligned to the project abstract and review deck. It demonstrates a single, inspectable pipeline:

**parameters or geometry file → mathematical profile → tool-radius offset / pocket raster → toolpath preview → ISO-style G-code → basic checks and time estimate**

## What works

- **Parametric spur gear**: pitch/base/addendum dimensions, sampled involute flanks, outside tool-radius offset, depth passes, roughing stock allowance and a rough tool/root-gap warning.
- **Thread milling**: helical `G02`/`G03` semicircles with Z advance per half pitch, using the review deck's illustrative radius formula `(nominal diameter - tool diameter)/2`.
- **Simplified blade**: a symmetric NACA-style 2D airfoil-like contour and outside profile machining.
- **2D import**: closed contours/linework from DXF or SVG; outside profile or raster pocket strategy with configurable step-over.
- **3D mesh preview**: STL/OBJ/PLY triangulated model preview and bounding extents. This mode intentionally does **not** generate arbitrary 3D machining code.
- **Exports and checks**: downloadable `.nc` file, geometry/toolpath preview, basic G-code text checks, and estimated cycle time.

## Quick start in VS Code (Windows)

1. Install **Python 3.11 or 3.12** and VS Code's Python extension.
2. Unzip the project and open the `CIM_GCode_Generator` folder in VS Code.
3. In the VS Code terminal run:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   streamlit run app.py
   ```

4. Open the local URL Streamlit prints (usually `http://localhost:8501`).

If PowerShell blocks activation, run `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` and `.\.venv\Scripts\python.exe -m streamlit run app.py` instead.

## macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

## Demonstration flow for your review

1. Select **Parametric spur gear**, vary module/tooth count/pressure angle and show the live profile preview.
2. Change tool diameter and depth-per-pass; point out the tool-fit warning and generated depth passes.
3. Select **Thread milling** with nominal diameter 10 mm, tool diameter 5 mm and pitch 1 mm. The path radius is 2.5 mm, as in your deck's worked example; show the helix and G-code.
4. Select **Import 2D geometry**, upload `examples/sample_contour.dxf` or `examples/sample_contour.svg`, and test Outside contour vs Inside pocket.
5. Download the `.nc` file, inspect the code and explain that the basic checks are not a substitute for a simulator.
6. Use **Preview 3D mesh** to show an STL/OBJ/PLY model. Explain that full arbitrary 3D machining is a later phase, because it needs stock/fixture/tool models and 3D CAM validation.

## Geometry file notes

- **DXF**: supported line, polyline, circle, arc, ellipse and spline linework are sampled and polygonized. Drawing dimensions are assumed to be mm unless you change the scale factor.
- **SVG**: path geometry is sampled; coordinates are treated as mm. Export a flattened/untransformed SVG with 1 unit = 1 mm. SVG transforms/CSS and document physical units are not converted into CAD units automatically.
- Closed loops are required. Open/disconnected contours are reported rather than silently guessed into closed profiles.
- Multiple contours can be imported, but nested islands/holes are not a complete CAM feature-recognition implementation.
- STL/OBJ/PLY extents are in the file's own units, because mesh files do not reliably encode unit metadata.

## Safety and engineering limitations

This is an educational prototype and outputs candidate G-code, not machine-certified programs. It currently uses a generic metric ISO-style dialect and assumes a three-axis milling context with a valid G54 work offset. It does not know your machine travels, tool length, holder, stock, clamps, spindle limits, material, cutter engagement, controller arc conventions or workholding.

Before any real cut: independently review the tool/part dimensions, offsets and depth; check tool diameter versus minimum feature width; use the actual controller's postprocessor; run a trusted G-code backplot and stock-removal simulation; and dry-run/single-block under qualified supervision. Do not run this prototype's output directly on a CNC machine.

## Project structure

```text
CIM_GCode_Generator/
├── app.py                 # Streamlit user interface and plots
├── core/
│   ├── geometry.py        # Gear/blade math, offsets and pocket paths
│   ├── importers.py       # DXF/SVG import and SVG-to-DXF conversion and mesh preview loader
│   └── gcode.py           # G-code writers and basic checks
├── examples/
│   ├── sample_contour.svg
│   ├── sample_contour.dxf
│   └── thread_milling_example.nc
├── tests/
│   └── test_core.py
├── requirements.txt
└── README.md
```

## Running the core tests

```bash
python -m unittest discover -s tests -v
```


## Updated CAD workflows

- **DXF import resilience:** expands block INSERTs where possible, samples supported line/arc/circle/ellipse/spline entities, and provides a diagnostic error listing entity types if no closed contour can be formed. Closed contours are required for automatic contour/pocket toolpaths.
- **SVG → DXF:** use the `SVG → DXF converter` workspace to preview closed SVG paths and export editable millimetre-based DXF polylines. SVG transforms/CSS and arbitrary SVG primitives are not a full CAD interchange implementation; check the preview and dimensions.
- **3D → 2D projection:** upload STL/OBJ/PLY and select top/front/side projection to create a projected footprint DXF. STEP is supported when optional `cadquery` is installed (`pip install cadquery`), otherwise export STEP to STL in FreeCAD first. This output is a projected footprint, not a dimensioned engineering drawing or hidden-line view.
- **Professional UI:** updated CIM Studio header, compact visual hierarchy, workspace picker and more actionable import troubleshooting.

### If DXF import says no usable linework

Open the drawing in CAD and confirm the part is in model space. Export a 2D DXF (R12 or 2010), explode block references if necessary, and convert intended outlines to closed polylines. Dimensions, text, hatches, 3D faces and arbitrary solids are not valid 2D cutting contours by themselves. If the file is a 3D model, use the 3D projection workspace first. Confirm units and use the geometry scale factor as needed.
