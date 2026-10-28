import unittest

from shapely.geometry import Polygon

from core.geometry import blade_polygon, contour_toolpaths, gear_polygon, pocket_toolpaths
from core.importers import load_dxf, geometry_to_dxf, load_svg, project_mesh_to_2d
import ezdxf
import io
from core.gcode import MachiningConfig, basic_gcode_checks, generate_contour_gcode, generate_thread_gcode


class TestGeometry(unittest.TestCase):
    def test_gear_polygon_is_valid(self):
        gear = gear_polygon(3.0, 20, 20.0)
        self.assertTrue(gear.is_valid)
        self.assertGreater(gear.area, 0)

    def test_blade_polygon_is_valid(self):
        blade = blade_polygon(60.0, 0.12)
        self.assertTrue(blade.is_valid)
        self.assertGreater(blade.area, 0)

    def test_contour_paths_and_gcode(self):
        part = Polygon([(0, 0), (30, 0), (30, 20), (0, 20)])
        rough, finish = contour_toolpaths(part, 4.0, "outside", 0.2)
        code, metrics = generate_contour_gcode(rough, MachiningConfig(), finish)
        self.assertIn("G21", code)
        self.assertIn("M30", code)
        self.assertFalse(basic_gcode_checks(code))
        self.assertGreater(metrics["cut_length_mm"], 0)

    def test_pocket_paths_remain_open(self):
        part = Polygon([(0, 0), (30, 0), (30, 20), (0, 20)])
        rough, finish = pocket_toolpaths(part, 4.0, 2.0, 0.2)
        self.assertTrue(rough)
        self.assertTrue(any(path[0] != path[-1] for path in rough))
        code, _ = generate_contour_gcode(rough, MachiningConfig(), finish)
        self.assertIn("G01 X", code)

    def test_dxf_import_preserves_nested_hole(self):
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_lwpolyline([(0, 0), (60, 0), (60, 35), (0, 35)], close=True)
        msp.add_circle((30, 17.5), 5)
        buffer = io.StringIO()
        doc.write(buffer)
        geom = load_dxf(buffer.getvalue().encode())
        self.assertEqual(len(geom.interiors), 1)
        self.assertGreater(geom.area, 1900)


    def test_svg_to_dxf_roundtrip(self):
        try:
            import svgpathtools  # noqa: F401
        except ImportError:
            self.skipTest("svgpathtools is installed by requirements.txt")
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10"><path d="M 0 0 L 20 0 L 20 10 L 0 10 Z"/></svg>'
        geom = load_svg(svg)
        encoded = geometry_to_dxf(geom)
        reloaded = load_dxf(encoded)
        self.assertGreater(reloaded.area, 100)

    def test_mesh_projection_returns_2d_geometry(self):
        import trimesh
        mesh = trimesh.creation.box(extents=(10, 20, 30))
        data = mesh.export(file_type="stl")
        projected = project_mesh_to_2d(data, "box.stl", "Top (XY)")
        self.assertAlmostEqual(projected.bounds[2] - projected.bounds[0], 10, places=2)
        self.assertAlmostEqual(projected.bounds[3] - projected.bounds[1], 20, places=2)

    def test_thread_helix_example(self):
        cfg = MachiningConfig(tool_diameter=5.0)
        code, xyz, metrics = generate_thread_gcode(10.0, 5.0, 1.0, 8.0, cfg)
        self.assertIn("I-2.5 J0", code)
        self.assertEqual(metrics["turns"], 8)
        self.assertAlmostEqual(metrics["path_radius_mm"], 2.5)
        self.assertGreater(len(xyz), 100)
        self.assertFalse(basic_gcode_checks(code))


if __name__ == "__main__":
    unittest.main()
