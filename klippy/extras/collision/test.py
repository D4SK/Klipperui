#!/usr/bin/env python3

import configparser
import copy
from os.path import dirname, realpath
import random
import unittest

import site
_klippo_dir = dirname(dirname(dirname(realpath(__file__))))
site.addsitedir(_klippo_dir)

import configfile

from extras.collision.geometry import Rectangle, Cuboid
from extras.collision.interface import CollisionInterface
from extras.collision.pathfinder import PathFinder


# Needed to create ConfigWrapper
class _DummyPrinter:
    reactor = None
    def register_event_handler(self, _, __):
        pass

class TestInterface(CollisionInterface):

    def __init__(self, config):
        super().__init__(config)
        self.handle_connect()

    def _read_printbed(self):
        stepper_configs = [self._config.getsection("stepper_" + axis)
                           for axis in "xyz"]
        min_ = [cfg.getfloat("position_min") for cfg in stepper_configs]
        max_ = [cfg.getfloat("position_max") for cfg in stepper_configs]
        return Cuboid(*min_, *max_)

CONFIG_FILE = "test_config.cfg"



class GeometryTest(unittest.TestCase):

    def test_rectangle(self):
        rectangle = Rectangle(10, 15, 14, 20)
        self.assertEqual(rectangle.x, 10)
        self.assertEqual(rectangle.y, 15)
        self.assertEqual(rectangle.width, 4)
        self.assertEqual(rectangle.height, 5)
        self.assertEqual(rectangle.max_x, 14)
        self.assertEqual(rectangle.max_y, 20)
        self.assertEqual(rectangle.get_area(), 20)

    def test_rectangle_negative(self):
        rectangle = Rectangle(5, 10, -15, -20)
        self.assertEqual(rectangle.x, -15)
        self.assertEqual(rectangle.y, -20)
        self.assertEqual(rectangle.width, 20)
        self.assertEqual(rectangle.height, 30)
        self.assertEqual(rectangle.max_x, 5)
        self.assertEqual(rectangle.max_y, 10)
        self.assertEqual(rectangle.get_area(), 600)

    def test_rectangle_bool(self):
        # Rectangle with positive area
        r1 = Rectangle(2, 3, 3, 4)
        # Rectangle with area 0
        r2 = Rectangle(2, 3, 2, 3)
        self.assertTrue(r1)
        self.assertFalse(r2)

    def test_rectangle_eq(self):
        r1 = Rectangle(4, 6, 8, 8)
        r2 = Rectangle(8, 8, 4, 6)
        self.assertEqual(r1, r2)

        r3 = Rectangle(4, 6, 8, 7)
        self.assertNotEqual(r1, r3)

    def test_rectangle_intersection(self):
        r1 = Rectangle(0, 0, 8, 4)
        r2 = Rectangle(2, 2, 6, 10)
        r3 = Rectangle(0, 8, 5, 10)  # bordering, but disjoint to r1
        r4 = Rectangle(50, 50, 60, 60)  # fully disjoint to r1
        r5 = Rectangle(1, 1, 7, 3)  # fully surrounded by r1
        r6 = Rectangle(8.9, 0, 12, 4)  # Less than 1 padding to r1
        r7 = Rectangle(0, 5.1, 8, 8)  # More than 1 padding to r1

        # Intersection
        expected = Rectangle(2, 2, 6, 4)
        self.assertEqual(r1.intersection(r2), expected)
        self.assertEqual(r2.intersection(r1), expected)
        self.assertEqual(r1.intersection(r3).get_area(), 0)
        self.assertEqual(r1.intersection(r4).get_area(), 0)
        self.assertEqual(r1.intersection(r5), r5)
        self.assertEqual(r5.intersection(r1), r5)
        self.assertEqual(r1.intersection(r1), r1)

        # Collision
        self.assertTrue(r1.collides_with(r2))
        self.assertTrue(r2.collides_with(r1))
        self.assertFalse(r1.collides_with(r3))
        self.assertFalse(r1.collides_with(r4))
        self.assertTrue(r1.collides_with(r5))
        self.assertTrue(r5.collides_with(r1))
        self.assertTrue(r1.collides_with(r1))

        # Collision with padding
        self.assertFalse(r1.collides_with(r6))
        self.assertTrue(r1.collides_with(r6, 1))
        self.assertTrue(r6.collides_with(r1, 1))
        self.assertFalse(r1.collides_with(r7, 1))


    def test_cuboid(self):
        cuboid = Cuboid(10, 15, 20, 30, 40, 30)
        self.assertEqual(cuboid.x, 10)
        self.assertEqual(cuboid.y, 15)
        self.assertEqual(cuboid.z, 20)
        self.assertEqual(cuboid.width, 20)
        self.assertEqual(cuboid.height, 25)
        self.assertEqual(cuboid.z_height, 10)

        self.assertEqual(cuboid.max_x, 30)
        self.assertEqual(cuboid.max_y, 40)
        self.assertEqual(cuboid.max_z, 30)
        self.assertEqual(cuboid.get_volume(), 5000)

    def test_cuboid_negative(self):
        cuboid = Cuboid(5, 20, 5, -5, 15, -15)
        self.assertEqual(cuboid.x, -5)
        self.assertEqual(cuboid.y, 15)
        self.assertEqual(cuboid.z, -15)
        self.assertEqual(cuboid.width, 10)
        self.assertEqual(cuboid.height, 5)
        self.assertEqual(cuboid.z_height, 20)

        self.assertEqual(cuboid.max_x, 5)
        self.assertEqual(cuboid.max_y, 20)
        self.assertEqual(cuboid.max_z, 5)
        self.assertEqual(cuboid.get_volume(), 1000)

    def test_cuboid_bool(self):
        c1 = Cuboid(2, 2, 2, 7, 5, 3)
        self.assertTrue(c1)
        c2 = Cuboid(2, 2, 2, 7, 2, 3)
        self.assertFalse(c2)

    def test_cuboid_eq(self):
        c1 = Cuboid(1, 2, 3, 4, 5, 6)
        c2 = Cuboid(1, 5, 3, 4, 2, 6)
        c3 = Cuboid(1, 1, 3, 4, 5, 6)
        self.assertEqual(c1, c2)
        self.assertNotEqual(c1, c3)

    def test_cuboid_intersection(self):
        c1 = Cuboid(0, 0, 0, 20, 15, 10)
        c2 = Cuboid(5, 5, 5, 15, 25, 35)  # Intersecting c1
        c3 = Cuboid(0, 15, 5, 20, 45, 45) # bordering but disjoint
        c4 = Cuboid(50, 50, 50, 60, 60, 60)  # fully disjoint to c1
        c5 = Cuboid(2, 2, 2, 14, 10, 8)  # fully surrounded by c1
        c6 = Cuboid(24.9, 19.9, 14.9, 40, 35, 30) # Too little padding
        c7 = Cuboid(25.1, 0, 0, 40, 15, 10)  # More than 5 padding

        # Intersection
        expected = Cuboid(5, 5, 5, 15, 15, 10)
        self.assertEqual(c1.intersection(c2), expected)
        self.assertEqual(c2.intersection(c1), expected)
        self.assertEqual(c1.intersection(c3).get_volume(), 0)
        self.assertEqual(c1.intersection(c4).get_volume(), 0)
        self.assertEqual(c1.intersection(c5), c5)
        self.assertEqual(c5.intersection(c1), c5)
        self.assertEqual(c1.intersection(c1), c1)

        # Collision
        self.assertTrue(c1.collides_with(c2))
        self.assertTrue(c2.collides_with(c1))
        self.assertFalse(c1.collides_with(c3))
        self.assertFalse(c1.collides_with(c4))
        self.assertTrue(c1.collides_with(c5))
        self.assertTrue(c5.collides_with(c1))
        self.assertTrue(c1.collides_with(c1))

        # Collision with padding
        self.assertFalse(c1.collides_with(c6))
        self.assertTrue(c1.collides_with(c6, 5))
        self.assertTrue(c6.collides_with(c1, 5))
        self.assertFalse(c1.collides_with(c7, 5))


class CollisionTest(unittest.TestCase):

    def setUp(self):
        fileconfig = configparser.RawConfigParser(strict=False)
        with open(CONFIG_FILE, "r") as fp:
            fileconfig.read_file(fp)
        config = configfile.ConfigWrapper(
            _DummyPrinter(), fileconfig, {}, "collision")
        self.collision = TestInterface(config).collision

        # Make another collision object but with an x-oriented gantry
        fc_x = copy.deepcopy(fileconfig)
        fc_x.set("collision", "gantry_orientation", "x")
        config_x = configfile.ConfigWrapper(
            _DummyPrinter(), fc_x, {}, "collision")
        self.collision_x = TestInterface(config_x).collision

    def test_attributes(self):
        c = self.collision
        self.assertIsInstance(c.printbed, Cuboid)
        self.assertIsInstance(c.printhead, Rectangle)
        self.assertIsInstance(c.gantry, Rectangle)
        self.assertIsInstance(c.gantry_x_oriented, bool)
        self.assertIsInstance(c.gantry_height, float)
        self.assertGreaterEqual(c.gantry_height, 0)
        self.assertEqual(c.current_objects, [])

        self.assertFalse(c.gantry_x_oriented)
        self.assertTrue(self.collision_x.gantry_x_oriented)

    def test_moving_parts(self):
        cy = self.collision
        cx = self.collision_x
        new_object = Cuboid(70, 100, 0, 150, 180, 60)
        self.assertEqual(cy.moving_parts(new_object),
                         (Rectangle(-10, 50.1, 176, 252),
                         Cuboid(41.5, 0, 84, 182, 1000, float('inf'))))
        self.assertEqual(cx.moving_parts(new_object),
                         (Rectangle(-10, 50.1, 176, 252),
                          Cuboid(0, 71.5, 84, 500, 212, float('inf'))))
        # Rectangle works too
        self.assertEqual(cy.moving_parts(new_object.projection()),
                         (Rectangle(-10, 50.1, 176, 252),
                         Cuboid(41.5, 0, 84, 182, 1000, float('inf'))))

    def test_collision(self):
        cy = self.collision
        cx = self.collision_x
        # Objects well distributed on X-Axis, only fits with cy
        objects0 = [Cuboid(10, 10, 0, 150, 100, 120),
                    Cuboid(250, 10, 0, 400, 200, 150)]
        # Like previous, but first object is small enough to fit under gantry
        objects1 = [Cuboid(10, 10, 0, 150, 100, 75),
                    Cuboid(250, 10, 0, 400, 200, 150)]
        # Gantry fits, printhead doesn't
        objects2 = [Cuboid(50, 50, 0, 150, 150, 75),
                    Cuboid(170, 50, 0, 300, 150, 75)]
        # Completely colliding objects
        objects3 = [Cuboid(10, 10, 0, 400, 400, 200),
                    Cuboid(50, 50, 0, 200, 200, 75)]
        # Diagonal setup: both gantry orientations fit
        objects4 = [Cuboid(10, 10, 0, 100, 100, 200),
                    Cuboid(200, 200, 0, 400, 400, 200)]
        # Would fit except the padding is lower than 5
        objects5 = [Cuboid(10, 10, 0, 100, 100, 80),
                    Cuboid(184, 10, 0, 300, 100, 80)]

        # Too large for printer
        o_large = Cuboid(10, 20, 0, 1500, 2000, 899)
        self.assertTrue(cy.object_collides(o_large))

        cy.add_object(objects0[0])
        cx.add_object(objects0[0])
        self.assertFalse(cy.object_collides(objects0[1]))
        # Gantry collides
        self.assertTrue(cx.object_collides(objects0[1]))
        cy.clear_objects()
        cx.clear_objects()

        cy.add_object(objects1[0])
        cx.add_object(objects1[0])
        self.assertFalse(cy.object_collides(objects1[1]))
        # Gantry passes over
        self.assertFalse(cx.object_collides(objects1[1]))
        cy.clear_objects()
        cx.clear_objects()

        cy.add_object(objects2[0])
        cx.add_object(objects2[0])
        # Printhead collides
        self.assertTrue(cy.object_collides(objects2[1]))
        self.assertTrue(cx.object_collides(objects2[1]))
        cy.clear_objects()
        cx.clear_objects()

        cy.add_object(objects3[0])
        cx.add_object(objects3[0])
        # Everything collides
        self.assertTrue(cy.object_collides(objects3[1]))
        self.assertTrue(cx.object_collides(objects3[1]))
        cy.clear_objects()
        cx.clear_objects()

        cy.add_object(objects4[0])
        cx.add_object(objects4[0])
        # Nothing collides
        self.assertFalse(cy.object_collides(objects4[1]))
        self.assertFalse(cx.object_collides(objects4[1]))
        cy.clear_objects()
        cx.clear_objects()

        cy.add_object(objects5[0])
        cx.add_object(objects5[0])
        # The padding isn't kept
        self.assertTrue(cy.object_collides(objects5[1]))
        self.assertTrue(cx.object_collides(objects5[1]))
        # Test that it would work with padding set from 5 to 3
        cy_no_pad = copy.copy(cy)
        cx_no_pad = copy.copy(cx)
        cy_no_pad.padding = 3
        cx_no_pad.padding = 3
        self.assertFalse(cy_no_pad.object_collides(objects5[1]))
        self.assertFalse(cx_no_pad.object_collides(objects5[1]))
        cy.clear_objects()
        cx.clear_objects()

        # The entire printbed shouldn't collide
        self.assertFalse(cy.object_collides(cy.printbed))
        self.assertFalse(cx.object_collides(cx.printbed))
        cy.clear_objects()
        cx.clear_objects()


class FinderTest(unittest.TestCase):

    setUp = CollisionTest.setUp

    def test_get_centering_offset(self):
        c = self.collision
        o1 = Rectangle(0, 0, 300, 400)
        o2 = Rectangle(10, 10, 490, 990)
        o3 = Cuboid(450, 900, 0, 550, 1100, 400)

        self.assertEqual(c.get_centering_offset(o1), (100, 300))
        self.assertEqual(c.get_centering_offset(o2), (0, 0))
        self.assertEqual(c.get_centering_offset(o3), (-250, -500))

    def test__condense_range(self):
        c = self.collision
        uncondensed = [[-2, 0], [-1, 0], [4, 10], [5, 8], [9, 12], [13, 18]]
        self.assertEqual(c._condense_ranges(uncondensed),
            [[-2, 0], [4, 12], [13, 18]])
        # Redefine the list because it might get mutated
        uncondensed = [[-2, 0], [-1, 0], [4, 10], [5, 8], [9, 12], [13, 18]]
        self.assertEqual(c._condense_ranges(uncondensed, 1),
            [[-2, 0], [4, 18]])

        # Unsorted list
        uncondensed = [[-2, 0], [-1, 0], [4, 10], [5, 8], [9, 12], [13, 18]]
        random.shuffle(uncondensed)
        self.assertEqual(c._condense_ranges(uncondensed),
            [[-2, 0], [4, 12], [13, 18]])

        # Special inputs
        self.assertEqual(c._condense_ranges([]), [])
        self.assertEqual(c._condense_ranges([[5, 10]]), [[5, 10]])

    def test_get_gantry_collisions(self):
        cy = self.collision
        cx = self.collision_x
        objects = [Cuboid(0, 0, 0, 50, 100, 100),
                   Cuboid(10, 580, 0, 70, 590, 100),
                   Cuboid(350, 10, 0, 370, 120, 100),
                   Cuboid(60, 110, 0, 150, 200, 100),
                   Cuboid(150, 110, 0, 250, 200, 50),  # Low enough
                   Cuboid(350, 400, 0, 400, 500, 100),
                   Cuboid(480, 400, 0, 490, 500, 100),
                   Cuboid(350, 580, 0, 400, 750, 100)]

        new_object = Rectangle(380, 800, 400, 820)

        for o in objects:
            cy.add_object(o)
            cx.add_object(o)

        # First don't specify object size
        self.assertEqual(cy.get_gantry_collisions(),
                         [Rectangle(-37, 0, 183.5, 1000),
                          Rectangle(313, 0, 433.5, 1000),
                          Rectangle(443, 0, 523.5, 1000)])
        self.assertEqual(cx.get_gantry_collisions(),
                         [Rectangle(0, -37, 500, 233.5),
                          Rectangle(0, 363, 500, 533.5),
                          Rectangle(0, 543, 500, 783.5)])

        # Test with object size
        self.assertEqual(cy.get_gantry_collisions(new_object),
                         [Rectangle(-37, 0, 183.5, 1000),
                          Rectangle(313, 0, 523.5, 1000)])
        self.assertEqual(cx.get_gantry_collisions(new_object),
                         [Rectangle(0, -37, 500, 233.5),
                          Rectangle(0, 363, 500, 783.5)])

    def test_get_side_offsets(self):
        cy = self.collision
        cx = self.collision_x
        objects = [Rectangle(0, 0, 10, 10),
                   Rectangle(100, 100, 150, 180),  # Too far out
                   Rectangle(120, 150, 150, 200),
                   Rectangle(170, 430, 330, 570),
                   Rectangle(310, 560, 400, 650),
                   Rectangle(410, 700, 490, 900)]

        # Construct object in center
        new_object = Rectangle(235, 454.9, 319, 523)
        mv_printhead, _ = cy.moving_parts(new_object)
        space = mv_printhead.grow(cy.padding)
        self.assertEqual(space, Rectangle(150, 400, 350, 600))

        self.assertEqual(set(cx._get_side_offsets(new_object, space, objects)),
                         {0, -230, -180, 180, -40})
        self.assertEqual(set(cy._get_side_offsets(new_object, space, objects)),
                         {0, -450, -170, 170, -40, 250})

    def test_find_offset(self):
        cy = self.collision
        cx = self.collision_x

        # SPECIAL CASES:
        # No objects at all
        new0 = self._object_from_space(Cuboid(
                200, 400, 0, 400, 600, 100))
        self.assertEqual(_round_tuple(cy.find_offset(new0)), (0, 0))
        self.assertEqual(_round_tuple(cx.find_offset(new0)), (0, 0))

        # Object is way too large
        new1 = Cuboid(-1000, -1000, 0, 4000, 5000, 8000)
        self.assertIsNone(cy.find_offset(new1))
        self.assertIsNone(cx.find_offset(new1))

        # Only needs centering to fit in printer
        new2 = Cuboid(400, 800, 0, 700, 1200, 100)
        expected = (-300, -500)
        self.assertEqual(cy.find_offset(new2), expected)
        self.assertEqual(cx.find_offset(new2), expected)

        # SIMPLE CASES - ALL LOWER THAN GANTRY
        # One move in both dimensions needed
        objects0 = [Cuboid(0, 0, 0, 500, 600, 50),
                    Cuboid(0, 650, 0, 300, 1000, 50)]
        for o in objects0:
            cy.add_object(o)
            cx.add_object(o)
        expected = (100, 200)
        self.assertEqual(cy.find_offset(new0), expected)
        self.assertEqual(cx.find_offset(new0), expected)
        self.assertTrue(cy.object_collides(new0))
        self.assertFalse(cy.object_collides(
            new0.translate(*expected, 0)))
        cy.clear_objects()
        cx.clear_objects()

        # Negative offsets in both dimensions needed
        objects1 = [Cuboid(300, 0, 0, 500, 1000, 50),
                    Cuboid(0, 500, 0, 300, 1000, 50)]
        for o in objects1:
            cy.add_object(o)
            cx.add_object(o)
        self.assertEqual(cy.find_offset(new0), (-100, -100))
        self.assertEqual(cx.find_offset(new0), (-100, -100))
        cy.clear_objects()
        cx.clear_objects()

        # Move in one dimension needed
        object2 = Cuboid(200, 0, 0, 300, 750, 50)
        cy.add_object(object2)
        cx.add_object(object2)
        self.assertEqual(cy.find_offset(new0), (100, 0))
        self.assertEqual(cx.find_offset(new0), (0, 350))
        cy.clear_objects()
        cx.clear_objects()

        # Barely space left
        objects3 = [Cuboid(0, 0, 0, 331, 1000, 50),  # Just enough
                    Cuboid(0, 0, 0, 332, 1000, 50)]  # Doesn't fit
        cy.add_object(objects3[0])
        cx.add_object(objects3[0])
        self.assertEqual(cy.find_offset(new0), (131, 0))
        self.assertEqual(cx.find_offset(new0), (131, 0))
        cy.add_object(objects3[1])
        cx.add_object(objects3[1])
        self.assertIsNone(cy.find_offset(new0))
        self.assertIsNone(cx.find_offset(new0))
        cy.clear_objects()
        cx.clear_objects()

        # GANTRY TESTS
        # Move in one dimension needed, but with gantry
        object4 = Cuboid(200, 100, 0, 400, 600, 100)
        cy.add_object(object4)
        cx.add_object(object4)
        # -206 because the gantry is 6 wider than the printhead here
        self.assertEqual(cy.find_offset(new0), (-206, 0))
        # In this case the gantry does not affect the result
        self.assertEqual(cx.find_offset(new0), (0, 200))
        cy.clear_objects()
        cx.clear_objects()

        # A negative offset is needed only in Y-orientation
        object5 = Cuboid(200, 100, 0, 500, 300, 100)
        cy.add_object(object5)
        cx.add_object(object5)
        # Gantry requires additional offset
        self.assertEqual(cy.find_offset(new0), (-206, 0))
        self.assertEqual(cx.find_offset(new0), (0, 0))
        cy.clear_objects()
        cx.clear_objects()

        # A positive offset is needed only in Y-orientation
        object6 = Cuboid(0, 100, 0, 300, 300, 100)
        cy.add_object(object6)
        cx.add_object(object6)
        # Gantry allows lower offset
        self.assertEqual(cy.find_offset(new0), (48.5, 0))
        self.assertEqual(cx.find_offset(new0), (0, 0))
        cy.clear_objects()
        cx.clear_objects()


    def _object_from_space(self, space):
        """Take a Cuboid of a space that an offset should be found for and
        create an object that would need this space by removing padding and
        printhead borders.
        """
        c = self.collision
        no_padding = space.grow(-c.padding)
        if (no_padding.width < c.printhead.width or
            no_padding.height < c.printhead.height):
            raise ValueError("Space too small!")
        return Cuboid(
                round(no_padding.x - c.printhead.x, 4),
                round(no_padding.y - c.printhead.y, 4),
                round(no_padding.z, 4),
                round(no_padding.max_x - c.printhead.max_x, 4),
                round(no_padding.max_y - c.printhead.max_y, 4),
                round(no_padding.max_z, 4))

def _round_tuple(numbers, ndigits=4):
    return tuple(round(n, ndigits) for n in numbers)


class PathFinderTest(unittest.TestCase):

    def setUp(self):
        """
        Explanation:
            1-0-###3    This depicts the rectangle at index 0 (-0-).
            0######2    The node indices of its corners are 0-3 as shown.

        The following scenario is set up here:

           ^                         goal
          y|                        X
           8
           |                13-3-###########
           |                12############14
           |
           6
           |#-1-####
           |#//////#
           |#//////#            21-5-###########    #-2-
           4#######6            #//////////////#    #//#
           |                    #//////////////#    #//#
           |                    20##############    8###
           |
           2
           |
           |
           |            #-4-        1-0-###3
           |            16##        0###X##2
           0--------2-------4-------6---^---8-------10-->
                                       start           x

        """
        objs = [
            Rectangle(6, 0, 8, 1),   #-0-
            Rectangle(0, 4, 2, 6),   #-1-
            Rectangle(10, 3, 11, 5), #-2-
            Rectangle(4, 7, 8, 8),   #-3-
            Rectangle(3, 0, 4, 1),   #-4-
            Rectangle(5, 3, 9, 5)]   #-5-
        start = (7, 0)
        goal = (6, 9)
        self.pf = PathFinder(objs)
        self.pf.set_route(start, goal)

    def test_edge_small(self):
        objs = [
            Rectangle(2, 4, 3, 6),
            Rectangle(1, 1, 2, 2),
            Rectangle(5, 1, 6, 6)]
        pf = PathFinder(objs)

        self.assertTrue(pf.edge(6, 8))
        self.assertTrue(pf.edge(7, 9))
        self.assertTrue(pf.edge(8, 9))
        self.assertTrue(pf.edge(5, 9))

        self.assertFalse(pf.edge(0, 3))
        self.assertFalse(pf.edge(4, 11))

    def test_edge(self):
        self.assertTrue(self.pf.edge(1, 6))
        self.assertTrue(self.pf.edge(1, 7))
        self.assertTrue(self.pf.edge(1, 19))
        self.assertTrue(self.pf.edge(1, 20))
        self.assertTrue(self.pf.edge(1, 3))
        self.assertTrue(self.pf.edge(3, 9))
        self.assertTrue(self.pf.edge(3, 19))
        self.assertTrue(self.pf.edge(19, 12)) # Vertical

        self.assertFalse(self.pf.edge(1, 2))
        self.assertFalse(self.pf.edge(1, 9))
        self.assertFalse(self.pf.edge(1, 12))
        self.assertFalse(self.pf.edge(1, 14))
        self.assertFalse(self.pf.edge(1, 23))
        self.assertFalse(self.pf.edge(2, 20))
        self.assertFalse(self.pf.edge(3, 14)) # Vertical

    def test_adj(self):
        for v in range(self.pf.n):
            adj = self.pf.adjacent(v)
            # Assert that E is irreflexive
            self.assertNotIn(v, [e[0] for e in adj])
            # Test symmetry of E
            for w, weight in adj:
                self.assertGreaterEqual(weight, 0)
                self.assertIn((v, weight), self.pf.adjacent(w))

    def test_astar(self):
        path = self.pf.shortest_path()
        # 24 = start, 25 = goal
        self.assertEqual(path, [24, 0, 20, 12, 13, 25])


if __name__ == '__main__':
    unittest.main()
