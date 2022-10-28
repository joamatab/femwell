from typing import Dict, Optional, Tuple, List

import numpy as np
import pygmsh
import gmsh
import shapely
from shapely.geometry import Point, LineString, Polygon
from shapely.ops import split

from collections import OrderedDict


class MeshTracker():
    def __init__(self, model, atol=1E-3):
        '''
        Map between shapely and gmsh 
        Shapely is useful for built-in geometry equivalencies and extracting orientation, instead of doing it manually
        '''
        self.shapely_points = []
        self.gmsh_points = []
        self.shapely_xy_lines = []
        self.gmsh_xy_lines = []
        self.gmsh_xy_surfaces = []
        self.model = model
        self.atol = atol

    """
    Retrieve existing geometry
    """
    def get_point_index(self, xy_point):
        for index, shapely_point in enumerate(self.shapely_points):
            if xy_point.equals_exact(shapely_point, self.atol) :
                return index
        return None

    def get_xy_line_index_and_orientation(self, xy_point1, xy_point2):
        xy_line = shapely.geometry.LineString([xy_point1, xy_point2])
        for index, shapely_line in enumerate(self.shapely_xy_lines):
            if xy_line.equals_exact(shapely_line, self.atol):
                first_xy_line, last_xy_line = xy_line.boundary.geoms
                first_xy, last_xy = shapely_line.boundary.geoms
                if first_xy_line.equals_exact(first_xy, self.atol):
                    return index, True
                else:
                    return index, False
        return None, 1

    """
    Channel loop utilities (no need to track)
    """
    def xy_channel_loop_from_vertices(self, vertices):
        edges = []
        for vertex1, vertex2 in [(vertices[i], vertices[i + 1]) for i in range(0, len(vertices)-1)]:
            gmsh_line, orientation = self.add_get_xy_line(vertex1, vertex2)
            if orientation:
                edges.append(gmsh_line)
            else:
                edges.append(-gmsh_line)
        channel_loop = self.model.add_curve_loop(edges)
        return channel_loop

    """
    Adding geometry
    """
    def add_get_point(self, shapely_xy_point, resolution=None):
        """
        Add a shapely point to the gmsh model, or retrieve the existing gmsh model points with equivalent coordinates (within tol.)

        Args:
            shapely_xy_point (shapely.geometry.Point): x, y coordinates
            resolution (float): gmsh resolution at that point
        """
        index = self.get_point_index(shapely_xy_point)
        if index is not None:
            gmsh_point = self.gmsh_points[index]
        else:
            if resolution is not None:
                gmsh_point = self.model.add_point([shapely_xy_point.x, shapely_xy_point.y], mesh_size=resolution)
            else:
                gmsh_point = self.model.add_point([shapely_xy_point.x, shapely_xy_point.y])
            self.shapely_points.append(shapely_xy_point)
            self.gmsh_points.append(gmsh_point)
        return gmsh_point


    def add_get_xy_line(self, shapely_xy_point1, shapely_xy_point2):
        """
        Add a shapely line to the gmsh model in the xy plane, or retrieve the existing gmsh model line with equivalent coordinates (within tol.)

        Args:
            shapely_xy_point1 (shapely.geometry.Point): first x, y coordinates
            shapely_xy_point2 (shapely.geometry.Point): second x, y coordinates
        """
        index, orientation = self.get_xy_line_index_and_orientation(shapely_xy_point1, shapely_xy_point2)
        if index is not None:
            gmsh_line = self.gmsh_xy_lines[index]
        else:
            gmsh_line = self.model.add_line(self.add_get_point(shapely_xy_point1), self.add_get_point(shapely_xy_point2))
            self.shapely_xy_lines.append(shapely.geometry.LineString([shapely_xy_point1, shapely_xy_point2]))
            self.gmsh_xy_lines.append(gmsh_line)
        return gmsh_line, orientation

    def add_xy_surface(self, shapely_xy_polygon, resolution=None):
        """
        Add a xy surface corresponding to shapely_xy_polygon, or retrieve the existing gmsh model surface with equivalent coordinates (within tol.)
        Note that there will never be collisions at this level, so no need to track

        Args:
            shapely_xy_polygon (shapely.geometry.Polygon):
        """
        # Create surface
        exterior_vertices = []
        hole_loops = []

        # Parse holes
        for polygon_hole in list(shapely_xy_polygon.interiors):
            hole_vertices = []
            for vertex in shapely.geometry.MultiPoint(polygon_hole.coords):
                gmsh_point = self.add_get_point(vertex, resolution=resolution)
                hole_vertices.append(vertex)
            hole_loops.append(self.xy_channel_loop_from_vertices(hole_vertices))
        # Parse boundary
        for vertex in shapely.geometry.MultiPoint(shapely_xy_polygon.exterior.coords):
            gmsh_point = self.add_get_point(vertex, resolution=resolution)
            exterior_vertices.append(vertex)
        channel_loop = self.xy_channel_loop_from_vertices(exterior_vertices)

        # Create surface
        plane_surface = self.model.add_plane_surface(channel_loop, holes=hole_loops)

        return plane_surface


def mesh_from_polygons(
    polygon_dict: OrderedDict,
    resolutions: Optional[Dict[str, float]] = None,
    default_resolution_min: float = 0.01,
    default_resolution_max: float = 0.1,
):

    import gmsh

    gmsh.initialize()

    geometry = pygmsh.occ.geometry.Geometry()
    geometry.characteristic_length_min = default_resolution_min
    geometry.characteristic_length_max = default_resolution_max

    model = geometry.__enter__()

    # Break up surfaces in order so that plane is tiled with non-overlapping layers
    polygons_tiled_dict = OrderedDict()
    full_edge_list = {}
    for lower_index, (lower_name, lower_polygon) in reversed(list(enumerate(polygon_dict.items()))):
        diff_polygon = lower_polygon
        for higher_index, (higher_name, higher_polygon) in reversed(list(enumerate(polygon_dict.items()))[:lower_index]):
            diff_polygon = diff_polygon.difference(higher_polygon)
        polygons_tiled_dict[lower_name] = diff_polygon
    
    # Add surfaces, reusing lines to simplify at early stage
    meshtracker = MeshTracker(model=model)
    for polygon_name, polygon in reversed(polygons_tiled_dict.items()):
        if polygon_name in resolutions.keys():
            resolution = resolutions[polygon_name]
        else:
            resolution = None
        plane_surface = meshtracker.add_xy_surface(polygon, resolution=resolution)
        model.add_physical(plane_surface, f"{polygon_name}")

    # Remove duplicated lines to clean up partially overlapping edges
    gmsh.model.occ.removeAllDuplicates()

    # Extract all unique lines (TODO: identify interfaces in label)
    i = 0
    for line in meshtracker.gmsh_xy_lines:
        model.add_physical(line, f"line_{i}")
        i += 1

    # Perform meshing
    geometry.generate_mesh(dim=2, verbose=True)

    return geometry

if __name__ == "__main__":

    import gmsh

    wsim = 2
    hclad = 2
    hbox = 2
    offset_core = -0.1
    wcore = 0.5
    hcore = 0.22
    core = Polygon([
            Point(-wcore/2, -hcore/2 + offset_core),
            Point(-wcore/2, hcore/2 + offset_core),
            Point(wcore/2, hcore/2 + offset_core),
            Point(wcore/2, -hcore/2 + offset_core),
        ])
    clad = Polygon([
            Point(-wsim/2, -hcore/2),
            Point(-wsim/2, -hcore/2 + hclad),
            Point(wsim/2, -hcore/2 + hclad),
            Point(wsim/2, -hcore/2),
        ])
    box = Polygon([
            Point(-wsim/2, -hcore/2),
            Point(-wsim/2, -hcore/2 - hbox),
            Point(wsim/2, -hcore/2 - hbox),
            Point(wsim/2, -hcore/2),
        ])

    polygons = OrderedDict()
    polygons["core"] = core 
    polygons["clad"] = clad
    polygons["box"] = box

    resolutions = {}
    resolutions["core"] = 0.01 
    resolutions["clad"] = 0.2


    mesh = mesh_from_polygons(polygons, resolutions)

    gmsh.write("mesh.msh")
    gmsh.clear()
    mesh.__exit__()

    import meshio

    mesh_from_file = meshio.read("mesh.msh")

    def create_mesh(mesh, cell_type, prune_z=True):
        cells = mesh.get_cells_type(cell_type)
        cell_data = mesh.get_cell_data("gmsh:physical", cell_type)
        points = mesh.points
        return meshio.Mesh(
            points=points,
            cells={cell_type: cells},
            cell_data={"name_to_read": [cell_data]},
        )

    line_mesh = create_mesh(mesh_from_file, "line", prune_z=True)
    meshio.write("facet_mesh.xdmf", line_mesh)

    triangle_mesh = create_mesh(mesh_from_file, "triangle", prune_z=True)
    meshio.write("mesh.xdmf", triangle_mesh)
