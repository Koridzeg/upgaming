from fastapi.testclient import TestClient

from investigator.main import app


def test_health_ok_on_fresh_database(empty_db):
    # Using TestClient as a context manager runs the lifespan: migrations, then the pool.
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "documents": 0}


def test_health_reports_503_when_database_is_unavailable(empty_db):
    with TestClient(app) as client:
        # Simulate losing the database: every acquire() on a closed pool raises.
        client.portal.call(app.state.pool.close)
        response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json()["database"] == "unreachable"
