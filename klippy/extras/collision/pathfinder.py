"""
TODO:
  * Get from print dimensions to needed space (add padding and printhead size)
  * Filter out corners that lie outside the printbed
  * Maybe filter out corners that lie inside another object's space
  * Add support for static objects (screws), possibly for collision as well
  * Check for gantry collisions in path
  * Add Z-scanning in case of failure
  * Generate G1-Code from path
  * Add custom G-Code command for finding a path
"""
from heapq import heappop, heappush
from math import sqrt
from typing import Optional, TypeVar, Generic, Any, cast

from .geometry import Rectangle
from .printerboxes import PrinterBoxes

PointType = tuple[float, float]

class PathFinderManager:

    def __init__(self, printer: PrinterBoxes) -> None:
        self.printer = printer

class PathFinder:
    """
    Find a path between two points by avoiding a given set of objects in the
    form of rectangles in a plane. This is done by constructing a graph from
    all corners of the rectangles as well as the two points between which we
    want a path. Two such vertices are connected by an edge in the graph if the
    straight line between them collides with no objects. The final path is
    found by applying the A*-algorithm on that graph.
    """

    def __init__(self, objects: list[Rectangle]):
        self.objects = objects

        # Vertices are mostly referenced by their integer index.
        # This list serves mostly as a symbol table.
        self.vertices: list[PointType] = []
        for o in self.objects:
            self.vertices.extend(o.get_corners())
        self.n: int = len(self.vertices)

        # Indices of start/goal vertex, if set
        self.start: Optional[int] = None
        self.goal: Optional[int] = None

        # Adjacency lists, one for each node. Each adjacent node is represented
        # together with the weight of that edge. The edge relation is lazily
        # evaluated through self.adjacent(). This list serves as a cache for
        # that function.
        self.adj: list[Optional[list[tuple[int, float]]]] = [None] * self.n

    def set_route(self, start: PointType, goal: PointType) -> None:
        equal = 0
        if self.start is not None:
            if self.vertices[self.start] == start:
                equal += 1
            else:
                # Simply replace old vertex
                self.vertices[self.start] = start
        else:
            self.vertices.append(start)
            self.start = self.n
            self.n += 1

        if self.goal is not None:
            if self.vertices[self.goal] == goal:
                equal += 1
            else:
                self.vertices[self.goal] = goal
        else:
            self.vertices.append(goal)
            self.goal = self.n
            self.n += 1

        if equal < 2:
            self.flush_caches()

    def add_object(self, obj: Rectangle) -> None:
        self.vertices.extend(obj.get_corners())
        self.n = len(self.vertices)
        self.flush_caches()

    def flush_caches(self) -> None:
        #TODO: Maybe don't throw everything away on graph changes
        self.adj = [None] * self.n

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
            # Function for line(x) = y on that line
            line = lambda x: m * (x - p1[0]) + p1[1]
        except ZeroDivisionError:
            # p1 and p2 lie on a vertical line (have the same x value)
            m = float('inf')

        for o in self.objects:
            if m == float('inf'):
                y = max(line_box.y, o.y)
                max_y = min(line_box.max_y, o.max_y)
                if o.x < line_box.x < o.max_x and max_y > y:
                    return False
                continue

            intersection = line_box.intersection(o)
            if not intersection:
                continue

            # Critical values: y values of the line at the x-values of the
            # intersection boundaries. These must lie either both above or
            # both below the object for the line to fit.
            c1 = line(intersection.x)
            c2 = line(intersection.max_x)
            if not ((c1 <= o.y and c2 <= o.y) or
                    (c1 >= o.max_y and c2 >= o.max_y)):
                return False

        return True

    def weight(self, v: int, w: int) -> float:
        """Calculate the weight of an edge"""
        return self.dist(self.vertices[v], self.vertices[w])

    @staticmethod
    def dist(p1: PointType, p2: PointType) -> float:
        """Euclidean distance between two points"""
        return sqrt((p2[0] - p1[0])**2 + (p2[1] - p1[1])**2)

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
            if v == self.goal:
                return dist[v], parent
            for w, weight in self.adjacent(v):
                if dist[w] > dist[v] + weight:
                    parent[w] = v
                    dist[w] = dist[v] + weight
                    # pq.push either adds w or updates its priority if w is
                    # already in the queue
                    pq.push(w, dist[w] + self.weight(w, self.goal))
        return float('inf'), parent

    def shortest_path(self) -> Optional[list[int]]:
        dist, parents = self.astar()
        if dist == float('inf'):
            return None
        # self.goal can't be None here, as that would have raised in astar()
        cur = cast(int, self.goal)
        path = [cur]
        while cur != self.start:
            new = parents[cur]
            assert new is not None
            path.append(new)
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
