from fastapi.testclient import TestClient

from src.app import app


def test_index_renders():
    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_book_renders():
    response = TestClient(app).get("/book/1")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
