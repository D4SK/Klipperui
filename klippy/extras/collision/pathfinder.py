"""
TODO:
  * Generate G1-Code from path
  * Add custom G-Code command for finding a path
"""
import copy
from heapq import heappop, heappush
from itertools import chain
import math
from typing import Union, Optional, TypeVar, Generic, Any, Sequence

from .geometry import Rectangle, Cuboid
from .printerboxes import PrinterBoxes

PointType = tuple[float, float]
Point3DType = tuple[float, float, float]

class PathFinderManager:

    def __init__(self, printer: PrinterBoxes) -> None:
        self.printer = printer

    def find_path(self,
        start: Point3DType,
        goal: Point3DType,
    ) -> Optional[list[Point3DType]]:
        # Verify that both points can be moved into
        if self.point_collides(start) or self.point_collides(goal):
            return None

        padding = self.printer.padding
        min_height = max(start[2], goal[2])
        inner_space = Cuboid(*start, *goal)
        min_gantry_space = self.printer.gantry_space(inner_space)
        gantry_collision_heights = iter(o.max_z for o in self.printer.objects
                                        if o.collides_with(min_gantry_space))
        min_gantry_height = max(gantry_collision_heights,
                                default=self.printer.gantry_height-padding)
        min_gantry_height += padding
        move_height = max(min_height,
                          min_gantry_height - self.printer.gantry_height)
        to_avoid = sorted(copy.copy(self.printer.objects), reverse=True,
                          key=lambda o: o.max_z)
        # Remove all objects that are too low to be relevant
        while to_avoid and to_avoid[-1].max_z + padding < move_height:
            to_avoid.pop()

        while True:
            # Must move higher than possible
            if move_height > self.printer.printbed.max_z:
                return None

            path = self.find_path_at_height(start[:2], goal[:2], to_avoid)
            if path is not None:
                return self.add_height_to_path(start, goal, path, move_height)

            if to_avoid:
                # Couldn't find path, move up above next object
                next_height = to_avoid.pop().max_z
                # If multiple objects have the same height, skip all of them
                while to_avoid and to_avoid[-1].max_z == next_height:
                    to_avoid.pop()
                move_height = next_height + padding
            else:
                return None

    def find_path_at_height(
        self, start: PointType, goal: PointType,
        objects: Sequence[Union[Rectangle, Cuboid]]
    ) -> Optional[list[PointType]]:
        # Add space for printhead to move around and padding
        spaces = [self.occupied_space(o) for o in objects]
        all_corners = chain.from_iterable(iter(o.get_corners() for o in spaces))
        vertices = [c for c in all_corners if self.filter_corner(c, spaces)]
        pf = PathFinder(vertices, spaces, start, goal)
        return pf.shortest_path()

    def add_height_to_path(
        self, start: Point3DType, goal: Point3DType,
        path: list[PointType], move_height: float
    ) -> list[Point3DType]:
        """Set the path to run at move_height.
        If start or goal aren't at that height, a vertical move up to that
        height is added at that end.

        TODO: Maybe add safe diagonal height changes
        """
        path_3d = [(x, y, move_height) for x, y in path]
        if start[2] != move_height:
            path_3d.insert(0, start)
        if goal[2] != move_height:
            path_3d.append(goal)
        return path_3d

    def point_collides(self, p: Point3DType) -> bool:
        """Return True if the printhead being at p would cause a collision.
        This is used to check for invalid start/goal points of a path search.
        """
        as_cube = Cuboid(*p, *p)
        return (not self.printer.printbed.contains(p)
                ) or self.printer.cuboid_collides(as_cube)

    def occupied_space(self, obj: Union[Rectangle, Cuboid]) -> Rectangle:
        """Return the space for an object that we can't move into, including
        the size of the printhead as well as padding.
        """
        ph = self.printer.printhead
        pad = self.printer.padding
        return Rectangle(obj.x - ph.max_x - pad,
                         obj.y - ph.max_y - pad,
                         obj.max_x - ph.x + pad,
                         obj.max_y - ph.y + pad)

    def filter_corner(self, p: PointType, objects: list[Rectangle]) -> bool:
        # Reject points that lie outside the printbed plane
        if not self.printer.printbed.projection().contains(p):
            return False
        # Reject points that lie strictly within a different object.
        # This isn't technically necessary as those points couldn't connect to
        # any others in the graph, but filtering that here should be faster as
        # it reduces the amount of vertices.
        for o in objects:
            if o.contains(p, include_edges=False):
                return False
        return True


class PathFinder:
    """
    Find a path between two points by avoiding a given set of objects in the
    form of rectangles in a plane. This is done by constructing a graph from
    all corners of the rectangles as well as the two points between which we
    want a path. Two such vertices are connected by an edge in the graph if the
    straight line between them collides with no objects. The final path is
    found by applying the A*-algorithm on that graph.
    """

    def __init__(self, vertices: list[PointType], objects: list[Rectangle],
                 start: PointType, goal: PointType):
        self.objects = objects

        # Vertices are generally referenced by their integer index.
        # This list serves mostly as a symbol table.
        self.vertices = vertices + [start, goal]
        self.n: int = len(self.vertices)

        # Indices of start/goal vertex, if set
        self.start: int = self.n - 2
        self.goal: int = self.n - 1

        # Adjacency lists, one for each node. Each adjacent node is represented
        # together with the weight of that edge. The edge relation is lazily
        # evaluated through self.adjacent(). This list serves as a cache for
        # that function.
        self.adj: list[Optional[list[tuple[int, float]]]] = [None] * self.n

    def adjacent(self, v: int) -> list[tuple[int, float]]:
        """Return all vertices adjacent to v.
        This is lazily computed because the entire edge relation is usually not
        required.
        """
        cached = self.adj[v]
        if cached is not None:
            return cached

        adjacent = [(w, self.weight(v, w))
                    for w in range(self.n)
                    if self.edge(v, w)]
        self.adj[v] = adjacent
        return adjacent

    def edge(self, v: int, w: int) -> bool:
        if v == w:
            return False

        p1 = self.vertices[v]
        p2 = self.vertices[w]
        line_box = Rectangle(*p1, *p2)

        try:
            # Inclination of the line between p1 and p2
            m = (p2[1] - p1[1]) / (p2[0] - p1[0])
        except ZeroDivisionError:
            # p1 and p2 lie on a vertical line (have the same x value)
            for o in self.objects:
                y = max(line_box.y, o.y)
                max_y = min(line_box.max_y, o.max_y)
                if o.x < line_box.x < o.max_x and max_y > y:
                    return False
            return True

        x1, y1 = p1
        for o in self.objects:
            intersection = line_box.intersection(o)
            if not intersection:
                continue

            # Critical values: y values of the line at the x-values of the
            # intersection boundaries. These must lie either both above or
            # both below the object for the line to fit.
            c1 = m * (intersection.x - x1) + y1
            c2 = m * (intersection.max_x - x1) + y1
            if not ((c1 <= o.y and c2 <= o.y) or
                    (c1 >= o.max_y and c2 >= o.max_y)):
                return False

        return True

    def weight(self, v: int, w: int) -> float:
        """Calculate the weight of an edge as the euclidean distance between
        its end points.
        """
        return math.dist(self.vertices[v], self.vertices[w])

    def astar(self) -> tuple[float, list[Optional[int]]]:
        if self.start is None or self.goal is None:
            raise ValueError("Start and endpoint not set (use set_route())")
        dist: list[float] = [float('inf')] * self.n
        dist[self.start] = 0
        parent: list[Optional[int]] = [None] * self.n
        pq = EditablePQ[int]()

        pq.push(self.start, self.weight(self.start, self.goal))
        while pq.queue:
            v = pq.pop()
            dist_v = dist[v]
            if v == self.goal:
                return dist_v, parent
            for w, weight in self.adjacent(v):
                if dist[w] > dist_v + weight:
                    parent[w] = v
                    dist[w] = dist_v + weight
                    # pq.push either adds w or updates its priority if w is
                    # already in the queue
                    pq.push(w, dist[w] + self.weight(w, self.goal))
        return float('inf'), parent

    def shortest_path(self) -> Optional[list[PointType]]:
        """Resolve the parent relation into a path and convert indices back to
        actual points.
        """
        dist, parents = self.astar()
        if dist == float('inf'):
            return None
        # self.goal can't be None here, as that would have raised in astar()
        cur = self.goal
        path = [self.vertices[cur]]
        while cur != self.start:
            new = parents[cur]
            assert new is not None
            path.append(self.vertices[new])
            cur = new
        path.reverse()
        return path


T = TypeVar('T')

class EditablePQ(Generic[T]):
    """A priority queue using a heap datastructure with added functionality for
    removing/updating elements.

    The mechanism for removing and updating elements is taken from the
    documentation of the heapq module.
    """
    _REMOVED = object()

    def __init__(self) -> None:
        self.queue: list[Any] = []
        self.entry_finder: dict[T, list[Any]] = {}

    def pop(self) -> T:
        """Return the item with the lowest associated priority score, filtering
        out any entries that have been marked as removed.
        """
        while self.queue:
            prio, item = heappop(self.queue)
            if item is not self._REMOVED:
                del self.entry_finder[item]
                return item
        raise IndexError("pop from empty priority queue")

    def push(self, item: T, priority: float) -> None:
        """Add new item or update the priority of an existing one"""
        entry = [priority, item]
        if item in self.entry_finder:
            self.remove(item)
        self.entry_finder[item] = entry
        heappush(self.queue, entry)

    def remove(self, item: T) -> None:
        """Marks the entry corresponding to the item as removed"""
        entry = self.entry_finder.pop(item)
        entry[1] = self._REMOVED
