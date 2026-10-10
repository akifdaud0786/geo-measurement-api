"""Per-feature measurement.

Every feature gets a MeasurementResult; nothing in here raises for a bad feature, so one
broken geometry never fails the whole file.
"""

from __future__ import annotations

from dataclasses import dataclass

import shapely
from pyproj import CRS, Geod
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.validation import explain_validity

from app.geo.crs import WGS84, choose_measurement_crs, crs_label, transform
from app.models import MeasurementStatus

POLYGONAL = {"Polygon", "MultiPolygon"}
LINEAR = {"LineString", "MultiLineString", "LinearRing"}
PUNTAL = {"Point", "MultiPoint"}

GEOD = Geod(ellps="WGS84")
# Features wider than this (~11 km) get their edges densified before reprojection.
DENSIFY_THRESHOLD_DEG = 0.1
DENSIFY_PARTS = 100


@dataclass
class MeasurementResult:
    status: MeasurementStatus
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    measurement_crs: str | None = None
    geodesic_area_m2: float | None = None
    geodesic_length_m: float | None = None
    note: str | None = None


def _polygonal_part(geom: BaseGeometry) -> BaseGeometry:
    """make_valid() can return a GeometryCollection; keep only its areal parts."""
    if geom.geom_type in POLYGONAL:
        return geom
    polys = [p for p in shapely.get_parts(geom) if p.geom_type in POLYGONAL]
    flat: list[Polygon] = []
    for p in polys:
        flat.extend(p.geoms if isinstance(p, MultiPolygon) else [p])
    return MultiPolygon(flat) if len(flat) != 1 else flat[0]


def _densify(geom: BaseGeometry) -> BaseGeometry:
    """Add vertices along long edges, in the source CRS, before reprojecting.

    A straight edge in the source CRS is a curve in any other CRS; reprojecting only the
    endpoints would silently straighten it. Segments are capped at 1/DENSIFY_PARTS of the
    feature's extent; only applied to large features, where the curvature is measurable.
    """
    minx, miny, maxx, maxy = geom.bounds
    extent = max(maxx - minx, maxy - miny)
    if extent == 0:
        return geom
    return shapely.segmentize(geom, extent / DENSIFY_PARTS)


def _oriented(geom: BaseGeometry) -> BaseGeometry:
    """Counter-clockwise exteriors so pyproj's geodesic area comes out positive."""
    if isinstance(geom, Polygon):
        return orient(geom, 1.0)
    return MultiPolygon([orient(p, 1.0) for p in geom.geoms])


def measure(geom: BaseGeometry | None, source_crs: CRS | None) -> MeasurementResult:
    if geom is None or geom.is_empty:
        return MeasurementResult(MeasurementStatus.SKIPPED, note="Feature has no geometry to measure.")

    gtype = geom.geom_type
    if gtype in PUNTAL:
        return MeasurementResult(
            MeasurementStatus.NOT_APPLICABLE, note="Points have no area or length."
        )
    if gtype not in POLYGONAL | LINEAR:
        return MeasurementResult(
            MeasurementStatus.UNSUPPORTED, note=f"Measurement is not supported for {gtype}."
        )
    if source_crs is None:
        return MeasurementResult(
            MeasurementStatus.ERROR,
            note="The file's CRS is unknown, so the geometry cannot be measured reliably.",
        )

    try:
        return _measure(shapely.force_2d(geom), source_crs)
    except Exception as exc:  # projection failures, GEOS errors, ...
        return MeasurementResult(MeasurementStatus.ERROR, note=f"Measurement failed: {exc}")


def _measure(geom: BaseGeometry, source_crs: CRS) -> MeasurementResult:
    notes: list[str] = []
    polygonal = geom.geom_type in POLYGONAL

    if polygonal and not geom.is_valid:
        reason = explain_validity(geom)
        geom = _polygonal_part(shapely.make_valid(geom))
        notes.append(f"Invalid polygon ({reason}) was repaired with make_valid before measuring.")
        if geom.is_empty:
            return MeasurementResult(
                MeasurementStatus.ERROR, note=notes[0] + " Nothing areal remained."
            )

    lonlat = transform(geom, source_crs, WGS84)
    minx, miny, maxx, maxy = lonlat.bounds
    if max(maxx - minx, maxy - miny) > DENSIFY_THRESHOLD_DEG:
        lonlat = transform(_densify(geom), source_crs, WGS84)
    target = choose_measurement_crs(lonlat)
    projected = transform(lonlat, WGS84, target)

    result = MeasurementResult(MeasurementStatus.MEASURED, measurement_crs=crs_label(target))
    if polygonal:
        result.area_m2 = projected.area
        result.perimeter_m = projected.length
        area, _perimeter = GEOD.geometry_area_perimeter(_oriented(lonlat))
        result.geodesic_area_m2 = abs(area)
    else:
        result.length_m = projected.length
        result.geodesic_length_m = GEOD.geometry_length(lonlat)
    result.note = " ".join(notes) or None
    return result
