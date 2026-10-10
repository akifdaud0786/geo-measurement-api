"""End-to-end API tests through FastAPI's TestClient."""

from __future__ import annotations

import pytest
import shapefile
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import FileFormat, FileStatus, UploadedFile
from tests.helpers import esri_prj, kml, kml_polygon, make_zip, shapefile_zip, square

SURVEY_KML = kml(
    "<Folder><name>Site A</name>"
    f'<Placemark id="pit"><name>Pit</name>{kml_polygon(square(77.59, 12.97, 0.01))}</Placemark>'
    "<Placemark><name>Haul road</name><LineString><coordinates>"
    "77.59,12.97 77.60,12.98</coordinates></LineString></Placemark>"
    "<Placemark><name>GCP 1</name><Point><coordinates>77.59,12.97</coordinates></Point></Placemark>"
    "</Folder>"
)


def _utm_line(w: shapefile.Writer):
    w.field("name", "C")
    w.line([[(650_000, 1_435_000), (650_300, 1_435_400)]])  # 500 m on the UTM grid
    w.record("fence")


def _utm_polygon(w: shapefile.Writer):
    w.field("name", "C")
    w.poly([square(650_000, 1_435_000, 100)])  # 100 m x 100 m on the UTM grid
    w.record("pad")


# ------------------------------------------------------------------ happy paths


def test_upload_kml_and_read_back(upload, client):
    resp = upload("survey.kml", SURVEY_KML)
    assert resp.status_code == 202
    body = resp.json()
    assert resp.headers["location"] == f"/api/files/{body['id']}/"
    assert body["status"] == "PENDING"

    info = client.get(f"/api/files/{body['id']}/").json()
    assert info["status"] == "COMPLETED"
    assert info["filename"] == "survey.kml"
    assert info["file_format"] == "kml"
    assert info["feature_count"] == 3
    assert info["crs"] == "EPSG:4326"
    assert info["created_at"].endswith("Z") or info["created_at"].endswith("+00:00")


def test_measurements(upload, client):
    file_id = upload("survey.kml", SURVEY_KML).json()["id"]
    body = client.get(f"/api/files/{file_id}/measurements/").json()

    assert body["crs"] == "EPSG:4326"
    assert body["units"] == {"area": "m2", "length": "m"}
    pit, road, gcp = body["measurements"]

    assert pit["feature_index"] == 0 and pit["source_id"] == "pit"
    assert pit["status"] == "measured"
    assert pit["area_m2"] == pytest.approx(1_200_290, rel=1e-4)
    assert pit["area_m2"] == pytest.approx(pit["geodesic_area_m2"], rel=1e-6)
    assert pit["measurement_crs"].startswith("+proj=laea")

    assert road["status"] == "measured"
    assert road["length_m"] == pytest.approx(road["geodesic_length_m"], rel=1e-5)
    assert road["area_m2"] is None

    assert gcp["status"] == "not_applicable"

    summary = body["summary"]
    assert summary == {
        "feature_count": 3,
        "measured": 2,
        "not_applicable": 1,
        "unsupported": 0,
        "skipped": 0,
        "errors": 0,
        "total_area_m2": pytest.approx(pit["area_m2"]),
        "total_length_m": pytest.approx(road["length_m"]),
    }


def test_projected_shapefile(upload, client):
    line_id = upload("fence.zip", shapefile_zip(shapefile.POLYLINE, _utm_line, prj=esri_prj(32643))).json()["id"]
    poly_id = upload("pad.zip", shapefile_zip(shapefile.POLYGON, _utm_polygon, prj=esri_prj(32643))).json()["id"]

    info = client.get(f"/api/files/{line_id}/").json()
    assert info["crs"] == "EPSG:32643"
    assert info["crs_name"] == "WGS 84 / UTM zone 43N"

    # Ground distance = grid distance / UTM scale factor (~0.9997 here), so close to, not exactly, 500.
    [fence] = client.get(f"/api/files/{line_id}/measurements/").json()["measurements"]
    assert fence["geometry_type"] == "LineString"
    assert fence["length_m"] == pytest.approx(500, rel=1e-3)
    assert fence["length_m"] == pytest.approx(fence["geodesic_length_m"], rel=1e-6)

    [pad] = client.get(f"/api/files/{poly_id}/measurements/").json()["measurements"]
    assert pad["geometry_type"] == "Polygon"
    assert pad["area_m2"] == pytest.approx(10_000, rel=2e-3)
    assert pad["area_m2"] == pytest.approx(pad["geodesic_area_m2"], rel=1e-6)


def test_features_endpoint_returns_geojson(upload, client):
    file_id = upload("survey.kml", SURVEY_KML).json()["id"]
    body = client.get(f"/api/files/{file_id}/features/").json()

    assert body["type"] == "FeatureCollection"
    assert body["crs"] == "EPSG:4326"
    pit = body["features"][0]
    assert pit["geometry"]["type"] == "Polygon"
    assert pit["geometry"]["coordinates"][0][0] == [77.59, 12.97]
    assert pit["properties"]["name"] == "Pit"
    assert pit["properties"]["folder"] == "Site A"
    assert pit["properties"]["_source_id"] == "pit"


def test_pagination(upload, client):
    file_id = upload("survey.kml", SURVEY_KML).json()["id"]
    body = client.get(f"/api/files/{file_id}/measurements/?limit=1&offset=1").json()
    assert [m["feature_index"] for m in body["measurements"]] == [1]
    assert body["summary"]["feature_count"] == 3  # summary always covers the whole file

    assert client.get(f"/api/files/{file_id}/measurements/?limit=0").status_code == 422


def test_kmz_upload(upload, client):
    file_id = upload("survey.kmz", make_zip({"doc.kml": SURVEY_KML})).json()["id"]
    info = client.get(f"/api/files/{file_id}/").json()
    assert info["file_format"] == "kmz"
    assert info["feature_count"] == 3


def test_list_and_delete(upload, client, settings):
    first = upload("a.kml", SURVEY_KML).json()["id"]
    second = upload("b.kml", SURVEY_KML).json()["id"]

    listing = client.get("/api/files/").json()
    assert listing["total"] == 2
    assert {item["id"] for item in listing["items"]} == {first, second}

    assert (settings.upload_dir / first).exists()
    assert client.delete(f"/api/files/{first}/").status_code == 204
    assert not (settings.upload_dir / first).exists()
    assert client.get(f"/api/files/{first}/").status_code == 404
    assert client.get("/api/files/").json()["total"] == 1


def test_client_supplied_path_is_not_trusted(upload, client, settings):
    file_id = upload("../../etc/survey.kml", SURVEY_KML).json()["id"]
    info = client.get(f"/api/files/{file_id}/").json()
    assert info["filename"] == "survey.kml"
    assert (settings.upload_dir / file_id / "survey.kml").exists()


# --------------------------------------------------------------- failure paths


@pytest.mark.parametrize(
    "filename, content, status, message",
    [
        ("survey.geojson", b"{}", 415, "Unsupported file type"),
        ("survey.kml", b"", 400, "empty"),
        ("site.zip", b"not a zip", 400, "not a valid zip"),
        ("site.zip", make_zip({"readme.txt": b"hi"}), 400, "does not contain a .shp"),
        ("site.zip", make_zip({"../x.shp": b""}), 400, "unsafe path"),
    ],
)
def test_upload_rejections(upload, client, settings, filename, content, status, message):
    resp = upload(filename, content)
    assert resp.status_code == status
    assert message in resp.json()["detail"]
    assert client.get("/api/files/").json()["total"] == 0
    assert not any(settings.upload_dir.iterdir())  # nothing left on disk


def test_upload_size_limit(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}",
        max_upload_bytes=100,
    )
    with TestClient(create_app(settings)) as client:
        resp = client.post("/api/files/", files={"file": ("big.kml", b"x" * 101)})
    assert resp.status_code == 413


def test_malformed_kml_marks_file_failed(upload, client):
    file_id = upload("broken.kml", b"<kml><Document>").json()["id"]

    info = client.get(f"/api/files/{file_id}/").json()
    assert info["status"] == "FAILED"
    assert "not well-formed" in info["error"]

    resp = client.get(f"/api/files/{file_id}/measurements/")
    assert resp.status_code == 409
    assert "processing failed" in resp.json()["detail"]


def test_bad_features_do_not_fail_the_file(upload, client):
    doc = kml(
        f"<Placemark>{kml_polygon(square(77.59, 12.97, 0.01))}</Placemark>"
        "<Placemark><gx:Track><gx:coord>77.5 12.9 0</gx:coord></gx:Track></Placemark>"
        "<Placemark><LineString><coordinates>oops</coordinates></LineString></Placemark>"
        "<Placemark><MultiGeometry><Point><coordinates>77.5,12.9</coordinates></Point>"
        "<LineString><coordinates>77.5,12.9 77.6,13</coordinates></LineString></MultiGeometry></Placemark>"
    )
    file_id = upload("mixed.kml", doc).json()["id"]
    body = client.get(f"/api/files/{file_id}/measurements/").json()

    assert [m["status"] for m in body["measurements"]] == ["measured", "skipped", "skipped", "unsupported"]
    assert "gx:Track is not supported" in body["measurements"][1]["note"]
    assert body["summary"]["measured"] == 1
    assert body["summary"]["skipped"] == 2
    assert body["summary"]["unsupported"] == 1


def test_unknown_crs_features_are_flagged(upload, client):
    def write(w):
        w.field("id", "N")
        w.poly([square(650_000, 1_435_000, 100)])
        w.record(1)

    file_id = upload("noprj.zip", shapefile_zip(shapefile.POLYGON, write)).json()["id"]
    info = client.get(f"/api/files/{file_id}/").json()
    assert info["status"] == "COMPLETED"
    assert info["crs"] is None
    assert "CRS is unknown" in info["warnings"][0]

    [m] = client.get(f"/api/files/{file_id}/measurements/").json()["measurements"]
    assert m["status"] == "error"
    assert m["area_m2"] is None


def test_unknown_file_is_404(client):
    for path in ("", "measurements/", "features/"):
        assert client.get(f"/api/files/doesnotexist/{path}").status_code == 404
    assert client.delete("/api/files/doesnotexist/").status_code == 404


def _insert_pending(app) -> str:
    with app.state.session_factory() as db:
        record = UploadedFile(
            filename="x.kml",
            file_format=FileFormat.KML,
            size_bytes=1,
            storage_path="unused",
            status=FileStatus.PROCESSING,
            warnings=[],
        )
        db.add(record)
        db.commit()
        return record.id


def test_results_while_processing_are_409(app, client):
    file_id = _insert_pending(app)
    for path in ("measurements/", "features/"):
        resp = client.get(f"/api/files/{file_id}/{path}")
        assert resp.status_code == 409
        assert "PROCESSING" in resp.json()["detail"]
    assert client.delete(f"/api/files/{file_id}/").status_code == 409


def test_unfinished_jobs_are_failed_on_restart(app, settings):
    with TestClient(app):
        file_id = _insert_pending(app)

    with TestClient(create_app(settings)) as client:
        info = client.get(f"/api/files/{file_id}/").json()
    assert info["status"] == "FAILED"
    assert "restart" in info["error"]


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
