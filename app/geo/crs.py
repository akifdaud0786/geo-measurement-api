"""CRS helpers: describing a CRS and choosing a projected CRS to measure in.

Strategy (per feature):

1. Transform the geometry from its source CRS to WGS84 longitude/latitude. This happens
   even when the source is already projected, because some projected CRSs (Web Mercator,
   EPSG:3857, is the classic case) distort area badly.
2. Project it to a Lambert Azimuthal Equal-Area (LAEA) CRS centred on the feature's own
   centroid, and measure there in metres.

Why LAEA centred on the feature rather than UTM: LAEA is equal-area, so polygon areas are
exact by construction wherever the feature is, and because the projection is centred on
the feature its length distortion is second-order in distance from the centre
(under 0.001% within ~50 km). UTM is conformal, not equal-area: a parcel in Bengaluru,
2.6 degrees from the UTM 43N central meridian, comes out 0.12% too large, and features
near zone edges, crossing zones or near the poles get worse. See README "Design decisions".
"""

from functools import lru_cache

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

WGS84 = CRS.from_epsg(4326)


def describe_crs(crs: CRS | None) -> tuple[str | None, str | None]:
    """Return ("EPSG:4326", "WGS 84")-style identifiers; the code is None for custom CRSs."""
    if crs is None:
        return None, None
    authority = crs.to_authority(min_confidence=70)
    code = f"{authority[0]}:{authority[1]}" if authority else None
    return code, crs.name


def local_equal_area_crs(lon: float, lat: float) -> CRS:
    return CRS.from_proj4(
        f"+proj=laea +lat_0={lat:.6f} +lon_0={lon:.6f} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )


def choose_measurement_crs(geom_lonlat: BaseGeometry) -> CRS:
    centre = geom_lonlat.centroid
    return local_equal_area_crs(centre.x, centre.y)


def crs_label(crs: CRS) -> str:
    code, _ = describe_crs(crs)
    return code or crs.srs


@lru_cache(maxsize=256)
def _transformer(src_wkt: str, dst_wkt: str) -> Transformer:
    return Transformer.from_crs(CRS.from_wkt(src_wkt), CRS.from_wkt(dst_wkt), always_xy=True)


def transform(geom: BaseGeometry, src: CRS, dst: CRS) -> BaseGeometry:
    """Reproject a 2D geometry. Raises ValueError if any coordinate cannot be transformed."""
    if src == dst:
        return geom
    transformer = _transformer(src.to_wkt(), dst.to_wkt())

    def _apply(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        out = np.column_stack([x, y])
        if not np.all(np.isfinite(out)):
            raise ValueError("Coordinates fall outside the valid area of the source CRS.")
        return out

    return shapely.transform(geom, _apply)
