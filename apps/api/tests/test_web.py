"""Built SPA navigation must work without masking missing assets."""

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from app.web import SPAStaticFiles


def test_spa_deep_link_and_asset_errors(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Application</h1>")
    client = TestClient(
        Starlette(
            routes=[
                Mount("/app-next", app=SPAStaticFiles(directory=tmp_path, html=True)),
            ]
        )
    )
    response = client.get("/app-next/floor-plans")
    assert response.status_code == 200
    assert response.text == "<h1>Application</h1>"
    assert client.get("/app-next/assets/missing.js").status_code == 404
    assert client.post("/app-next/floor-plans").status_code == 405
