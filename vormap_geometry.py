"""Shared geometry helpers used across VoronoiMap extension modules.

Extracted to eliminate copy-pasted implementations of the Shoelace formula,
polygon perimeter, centroid, and related computations that were duplicated
in 4+ modules.
"""

import math


def polygon_area(vertices):
    """Compute polygon area using the Shoelace formula.

    Parameters
    ----------
    vertices : list[tuple[float, float]]
        Ordered polygon vertices (x, y).

    Returns
    -------
    float
        Absolute area of the polygon. Returns 0.0 for fewer than 3 vertices.
    """
    n = len(vertices)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += vertices[i][0] * vertices[j][1]
        area -= vertices[j][0] * vertices[i][1]
    return abs(area) / 2.0


def polygon_perimeter(vertices):
    """Compute the perimeter of a polygon.

    Parameters
    ----------
    vertices : list[tuple[float, float]]
        Ordered polygon vertices.

    Returns
    -------
    float
        Sum of edge lengths. Returns 0.0 for fewer than 2 vertices.
    """
    n = len(vertices)
    if n < 2:
        return 0.0
    p = 0.0
    for i in range(n):
        j = (i + 1) % n
        dx = vertices[j][0] - vertices[i][0]
        dy = vertices[j][1] - vertices[i][1]
        p += math.sqrt(dx * dx + dy * dy)
    return p


def polygon_centroid(vertices):
    """Centroid of a simple polygon using the Shoelace-weighted formula.

    Parameters
    ----------
    vertices : list[tuple[float, float]]
        Ordered polygon vertices.

    Returns
    -------
    tuple[float, float]
        (cx, cy) centroid. Falls back to arithmetic mean for degenerate cases.
    """
    n = len(vertices)
    if n == 0:
        return (0.0, 0.0)
    if n == 1:
        return vertices[0]
    if n == 2:
        return ((vertices[0][0] + vertices[1][0]) / 2,
                (vertices[0][1] + vertices[1][1]) / 2)
    cx, cy, a6 = 0.0, 0.0, 0.0
    for i in range(n):
        j = (i + 1) % n
        cross = (vertices[i][0] * vertices[j][1] -
                 vertices[j][0] * vertices[i][1])
        cx += (vertices[i][0] + vertices[j][0]) * cross
        cy += (vertices[i][1] + vertices[j][1]) * cross
        a6 += cross
    if abs(a6) < 1e-12:
        return (sum(v[0] for v in vertices) / n,
                sum(v[1] for v in vertices) / n)
    cx /= (3.0 * a6)
    cy /= (3.0 * a6)
    return (cx, cy)


def isoperimetric_quotient(area, perimeter):
    """Compute the isoperimetric quotient (circularity measure).

    IQ = 4 * pi * area / perimeter^2

    A perfect circle has IQ = 1.0. Lower values indicate less compact shapes.

    Parameters
    ----------
    area : float
    perimeter : float

    Returns
    -------
    float
        Value in [0, 1]. Returns 0.0 if perimeter is zero.
    """
    if perimeter < 1e-12:
        return 0.0
    return 4.0 * math.pi * area / (perimeter * perimeter)


def edge_length(v1, v2):
    """Euclidean distance between two 2D vertices.

    Parameters
    ----------
    v1, v2 : tuple[float, float]

    Returns
    -------
    float
    """
    dx = v2[0] - v1[0]
    dy = v2[1] - v1[1]
    return math.sqrt(dx * dx + dy * dy)


def build_data_index(data):
    """Build a coordinate-to-index lookup for seed point data.

    Only records the *first* occurrence of each seed coordinate, matching
    the semantics of the previous ``list.index()`` approach.  Building
    the dict is O(n); each subsequent lookup is O(1).

    Parameters
    ----------
    data : list[tuple[float, float]]
        Seed points.

    Returns
    -------
    dict[tuple, int]
        Maps coordinate tuples to their index in the data list.
    """
    lookup = {}
    for i, pt in enumerate(data):
        key = tuple(pt)
        if key not in lookup:
            lookup[key] = i
    return lookup
