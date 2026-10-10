"""Generate the sample files in samples/ (run: python scripts/make_samples.py)."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import shapefile
from pyproj import CRS

OUT = Path(__file__).resolve().parent.parent / "samples"

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Quarry survey - flight 12</name>
    <Folder>
      <name>Pit area</name>
      <Placemark id="pit-boundary">
        <name>Pit boundary</name>
        <ExtendedData><Data name="surveyed_on"><value>2026-09-28</value></Data></ExtendedData>
        <Polygon>
          <outerBoundaryIs><LinearRing><coordinates>
            77.5900,12.9700,0 77.5990,12.9700,0 77.5990,12.9790,0 77.5900,12.9790,0 77.5900,12.9700,0
          </coordinates></LinearRing></outerBoundaryIs>
          <innerBoundaryIs><LinearRing><coordinates>
            77.5930,12.9730,0 77.5930,12.9760,0 77.5960,12.9760,0 77.5960,12.9730,0 77.5930,12.9730,0
          </coordinates></LinearRing></innerBoundaryIs>
        </Polygon>
      </Placemark>
      <Placemark id="haul-road">
        <name>Haul road</name>
        <LineString><coordinates>
          77.5900,12.9700 77.5850,12.9650 77.5800,12.9655 77.5750,12.9600
        </coordinates></LineString>
      </Placemark>
    </Folder>
    <Folder>
      <name>Control</name>
      <Placemark id="gcp-1"><name>GCP 1</name><Point><coordinates>77.5900,12.9700,912</coordinates></Point></Placemark>
      <Placemark id="gcp-2"><name>GCP 2</name><Point><coordinates>77.5990,12.9790,915</coordinates></Point></Placemark>
    </Folder>
  </Document>
</kml>
"""


def build_shapefile_zip() -> bytes:
    """Stockpile footprints in WGS 84 / UTM zone 43N (EPSG:32643)."""
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON)
    w.field("name", "C", size=40)
    w.field("material", "C", size=20)
    stockpiles = [
        ("Stockpile A", "iron ore", 780_800, 1_434_900, 60, 40),
        ("Stockpile B", "overburden", 780_900, 1_434_950, 120, 80),
        ("Stockpile C", "iron ore", 781_050, 1_434_880, 45, 45),
    ]
    for name, material, x, y, dx, dy in stockpiles:
        w.poly([[(x, y), (x, y + dy), (x + dx, y + dy), (x + dx, y), (x, y)]])  # clockwise outer ring
        w.record(name, material)
    w.close()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("stockpiles.shp", shp.getvalue())
        zf.writestr("stockpiles.shx", shx.getvalue())
        zf.writestr("stockpiles.dbf", dbf.getvalue())
        zf.writestr("stockpiles.prj", CRS.from_epsg(32643).to_wkt("WKT1_ESRI"))
        zf.writestr("stockpiles.cpg", "UTF-8")
    return buf.getvalue()


def main() -> int:
    OUT.mkdir(exist_ok=True)
    (OUT / "quarry_survey.kml").write_text(KML, encoding="utf-8")
    (OUT / "stockpiles_utm43n.zip").write_bytes(build_shapefile_zip())
    print(f"Wrote samples to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
