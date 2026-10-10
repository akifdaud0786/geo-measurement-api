"""Measurement and CRS-handling tests.

The reference values come from pyproj.Geod (geodesic calculations on the WGS84 ellipsoid),
which is independent of the projection pipeline under test.
"""

from __future__ import annotations

import pytest
import shapely
from pyproj import CRS, Geod, Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.polygon import orient

from app.geo.crs import choose_measurement_crs
from app.geo.measure import measure
from app.models import MeasurementStatus
from tests.helpers import square

WGS84 = CRS.from_epsg(4326)
GEOD = Geod(ellps="WGS84")


def geodesic_area(poly, densify: float | None = None) -> float:
    """Geodesic area; `densify` (degrees) makes geodesic edges follow straight lon/lat edges."""
    if densify:
        poly = shapely.segmentize(poly, densify)
    return abs(GEOD.geometry_area_perimeter(orient(poly, 1.0))[0])


def test_lonlat_polygon_area_matches_geodesic():
    # ~1.1 km x 1.1 km block in Bengaluru, coordinates in degrees.
    poly = Polygon(square(77.59, 12.97, 0.01))
    result = measure(poly, WGS84)

    assert result.status is MeasurementStatus.MEASURED
    assert result.area_m2 == pytest.approx(geodesic_area(poly), rel=1e-6)
    assert result.area_m2 == pytest.approx(1.2003e6, rel=1e-3)
    assert result.measurement_crs.startswith("+proj=laea")
    # Sanity: the answer is nowhere near the meaningless "area in square degrees".
    assert result.area_m2 > 1_000_000 * poly.area


def test_lonlat_line_length_matches_geodesic():
    line = LineString([(77.59, 12.97), (77.60, 12.98), (77.62, 12.98)])
    result = measure(line, WGS84)

    assert result.status is MeasurementStatus.MEASURED
    assert result.length_m == pytest.approx(GEOD.geometry_length(line), rel=1e-5)
    assert result.geodesic_length_m == pytest.approx(GEOD.geometry_length(line))
    assert result.area_m2 is None


def test_projected_utm_source_is_measured_on_the_ground():
    # 100 m x 100 m *grid* square in UTM 43N. UTM grid distance != ground distance
    # (scale factor ~0.9996-1.0006), so the true area is close to, but not exactly, 10 000 m2.
    utm = CRS.from_epsg(32643)
    poly = Polygon(square(650_000, 1_435_000, 100))
    to_lonlat = Transformer.from_crs(utm, WGS84, always_xy=True)
    lonlat = Polygon([to_lonlat.transform(x, y) for x, y in poly.exterior.coords])

    result = measure(poly, utm)

    assert result.area_m2 == pytest.approx(geodesic_area(lonlat), rel=1e-6)
    assert result.area_m2 == pytest.approx(10_000, rel=2e-3)


def test_web_mercator_source_is_not_measured_naively():
    # In EPSG:3857 a 1000 x 1000 unit square at 60N is only ~500 m x 500 m on the ground.
    mercator = CRS.from_epsg(3857)
    to_merc = Transformer.from_crs(WGS84, mercator, always_xy=True)
    x0, y0 = to_merc.transform(10.0, 60.0)
    poly = Polygon(square(x0, y0, 1000))

    result = measure(poly, mercator)

    assert poly.area == pytest.approx(1_000_000)  # what a naive planar calculation gives
    assert result.area_m2 == pytest.approx(250_000, rel=1e-2)
    assert result.area_m2 == pytest.approx(result.geodesic_area_m2, rel=1e-6)


def test_large_polygon_area_stays_exact_with_equal_area_projection():
    # 10 x 10 degrees - spans several UTM zones; equal-area projection keeps area exact.
    # Edges are straight in lon/lat (the file's CRS), so the reference is densified to match.
    poly = Polygon(square(70, 10, 10))
    result = measure(poly, WGS84)
    assert result.area_m2 == pytest.approx(geodesic_area(poly, densify=0.01), rel=1e-4)


def test_polar_polygon_is_measured():
    poly = Polygon(square(20, 85, 1))  # UTM is undefined above 84N
    result = measure(poly, WGS84)
    assert result.status is MeasurementStatus.MEASURED
    assert result.area_m2 == pytest.approx(geodesic_area(poly, densify=0.001), rel=1e-5)


def test_polygon_with_hole():
    outer = square(77.59, 12.97, 0.01)
    hole = square(77.592, 12.972, 0.004)
    poly = Polygon(outer, [hole[::-1]])
    result = measure(poly, WGS84)

    expected = geodesic_area(Polygon(outer)) - geodesic_area(Polygon(hole))
    assert result.area_m2 == pytest.approx(expected, rel=1e-6)
    assert result.geodesic_area_m2 == pytest.approx(expected, rel=1e-9)


def test_multipolygon_area_is_sum_of_parts():
    a, b = Polygon(square(77.59, 12.97, 0.01)), Polygon(square(77.70, 12.97, 0.01))
    result = measure(MultiPolygon([a, b]), WGS84)
    assert result.area_m2 == pytest.approx(geodesic_area(a) + geodesic_area(b), rel=1e-5)


def test_perimeter_is_reported_for_polygons():
    result = measure(Polygon(square(77.59, 12.97, 0.01)), WGS84)
    assert result.perimeter_m == pytest.approx(4 * 1090, rel=0.02)


def test_invalid_bowtie_polygon_is_repaired():
    bowtie = Polygon([(77.59, 12.97), (77.60, 12.98), (77.60, 12.97), (77.59, 12.98), (77.59, 12.97)])
    assert not bowtie.is_valid

    result = measure(bowtie, WGS84)

    assert result.status is MeasurementStatus.MEASURED
    assert "repaired" in result.note
    # Two triangles, each a quarter of the bounding square.
    assert result.area_m2 == pytest.approx(geodesic_area(Polygon(square(77.59, 12.97, 0.01))) / 2, rel=1e-3)


@pytest.mark.parametrize(
    "geom, status",
    [
        (Point(77.59, 12.97), MeasurementStatus.NOT_APPLICABLE),
        (MultiPoint([(77.59, 12.97), (77.6, 12.98)]), MeasurementStatus.NOT_APPLICABLE),
        (GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])]), MeasurementStatus.UNSUPPORTED),
        (Polygon(), MeasurementStatus.SKIPPED),
        (None, MeasurementStatus.SKIPPED),
    ],
)
def test_non_measurable_geometries_are_handled(geom, status):
    result = measure(geom, WGS84)
    assert result.status is status
    assert result.area_m2 is None and result.length_m is None
    assert result.note


def test_unknown_crs_is_an_error_not_a_guess():
    result = measure(Polygon(square(1000, 1000, 10)), None)
    assert result.status is MeasurementStatus.ERROR
    assert "CRS is unknown" in result.note


def test_coordinates_outside_crs_domain_are_reported():
    result = measure(LineString([(0, 95), (1, 96)]), WGS84)  # latitude > 90
    assert result.status is MeasurementStatus.ERROR
    assert result.note.startswith("Measurement failed")


def test_measurement_crs_is_centred_on_feature():
    crs = choose_measurement_crs(Polygon(square(77.59, 12.97, 0.01)))
    assert crs.is_projected
    assert "+proj=laea +lat_0=12.975000 +lon_0=77.595000" in crs.srs
