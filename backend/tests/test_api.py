"""The HTTP API end to end: real app, routing, validation and test database; fake models."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from investigator.api.ratelimit import RateLimiter
from investigator.api.services import Services, StatsRefresher
from investigator.ingestion.tokenizer import WhitespaceTokenizer
from investigator.main import SinglePageApp, create_app
from tests.fakes import FakeEmbedder
from tests.test_agent import ScriptedLLM, call, reply

RUNBOOK = b"---\ntitle: Gateway runbook\n---\n# Gateway runbook\n\nPool max_size is 200."
RUNBOOK_V2 = b"---\ntitle: Gateway runbook\n---\n# Gateway runbook\n\nPool max_size is 150."


def client_with(llm=None, per_minute: int = 5) -> TestClient:
    async def factory(settings, pool):
        return Services(
            settings=settings,
            pool=pool,
            embedder=FakeEmbedder(),
            tokenizer=WhitespaceTokenizer(),
            llm=llm,
            limiter=RateLimiter(per_minute=per_minute, per_day=100),
            stats=StatsRefresher(pool, settings.chunk_strategies),
        )

    return TestClient(create_app(factory))


def upload(client, name: str, data: bytes):
    return client.post("/api/documents", files={"file": (name, data, "text/markdown")})


def test_ui_deep_links_load_the_app_but_unknown_api_paths_stay_404(tmp_path):
    (tmp_path / "index.html").write_text("<app-root></app-root>")
    (tmp_path / "main.js").write_text("bootstrap()")
    app = FastAPI()
    app.mount("/", SinglePageApp(directory=tmp_path, html=True))

    with TestClient(app) as client:
        root, asset = client.get("/"), client.get("/main.js")
        deep_link, unknown_api = client.get("/documents"), client.get("/api/nope")

    assert root.text == deep_link.text == "<app-root></app-root>"  # browser refresh on /documents
    assert asset.text == "bootstrap()"
    assert unknown_api.status_code == 404


def test_health_reports_database_and_configuration(empty_db):
    with client_with() as client:
        body = client.get("/api/health").json()

    assert body == {
        "status": "ok",
        "database": "ok",
        "documents": 0,
        "llm": "not configured",
        "embedding_model_matches_index": True,
    }


def test_health_is_503_when_the_database_is_gone(empty_db):
    with client_with() as client:
        client.portal.call(client.app.state.services.pool.close)
        response = client.get("/api/health")

    assert response.status_code == 503 and response.json()["database"] == "unreachable"


def test_document_lifecycle_add_list_update_delete(empty_db):
    with client_with() as client:
        created = upload(client, "runbook.md", RUNBOOK)
        same_again = upload(client, "runbook.md", RUNBOOK)
        document_id = created.json()["document_id"]
        listed = client.get("/api/documents", params={"source_type": "upload"}).json()
        updated = client.put(
            f"/api/documents/{document_id}", files={"file": ("v2.md", RUNBOOK_V2, "text/markdown")}
        )
        detail = client.get(f"/api/documents/{document_id}").json()
        deleted = client.delete(f"/api/documents/{document_id}")
        gone = client.get(f"/api/documents/{document_id}")

    assert created.status_code == 201 and created.json()["status"] == "created"
    assert same_again.json()["status"] == "unchanged"
    assert listed["total"] == 1 and listed["items"][0]["title"] == "Gateway runbook"
    assert updated.json()["status"] == "updated" and updated.json()["version"] == 2
    assert "150" in detail["content"] and [v["version"] for v in detail["versions"]] == [1]
    assert deleted.status_code == 204 and gone.status_code == 404


def test_bad_uploads_get_clear_client_errors(empty_db):
    with client_with() as client:
        unsupported = upload(client, "malware.exe", b"MZ...")
        client.app.state.services.settings.max_upload_mb = 0
        too_big = upload(client, "big.md", RUNBOOK)
        client.app.state.services.settings.max_upload_mb = 10

    assert unsupported.status_code == 422 and "Supported" in unsupported.json()["detail"]
    assert too_big.status_code == 413


def test_search_explains_its_ranking_and_hides_vectors(empty_db):
    with client_with() as client:
        upload(client, "runbook.md", RUNBOOK)
        hits = client.post("/api/search", json={"query": "pool max_size", "k": 3}).json()

    assert hits[0]["title"] == "Gateway runbook"
    assert "fused" in hits[0]["scores"] and "embedding" not in hits[0]


def scripted_answer():
    return ScriptedLLM(
        reply(call("search", {"query": "pool size"})),
        reply(call("final_answer", {"answer": "The pool is 200 [E1].", "status": "answered"})),
        reply(content='{"claims": [{"claim": 1, "verdict": "supported", "reason": "stated"}]}'),
    )


def test_investigate_returns_a_cited_answer(empty_db):
    with client_with(llm=scripted_answer()) as client:
        upload(client, "runbook.md", RUNBOOK)
        result = client.post("/api/investigate", json={"question": "What is the pool size?"}).json()

    assert result["status"] == "answered"
    assert result["citations"][0]["title"] == "Gateway runbook"
    assert result["verification"][0]["verdict"] == "supported"


def test_investigation_streams_server_sent_events(empty_db):
    with client_with(llm=scripted_answer()) as client:
        upload(client, "runbook.md", RUNBOOK)
        with client.stream(
            "GET", "/api/investigate/stream", params={"question": "What is the pool size?"}
        ) as response:
            body = "".join(response.iter_text())

    assert response.headers["content-type"].startswith("text/event-stream")
    assert (
        body.index("event: search") < body.index("event: verification") < body.index("event: final")
    )


def test_rate_limit_protects_the_llm_quota(empty_db):
    llm = ScriptedLLM(*[reply(call("final_answer", {"answer": "x", "status": "partial"}))] * 3)
    with client_with(llm=llm, per_minute=2) as client:
        codes = [
            client.post(
                "/api/investigate", json={"question": "Anything?", "verify": False}
            ).status_code
            for _ in range(3)
        ]
        limited = client.post("/api/investigate", json={"question": "Anything?"})

    assert codes == [200, 200, 429]
    assert int(limited.headers["retry-after"]) > 0


@pytest.mark.parametrize("path", ["/api/investigate"])
def test_without_an_llm_key_investigations_are_503(empty_db, path):
    with client_with(llm=None) as client:
        response = client.post(path, json={"question": "Anything?"})

    assert response.status_code == 503 and "GROQ_API_KEY" in response.json()["detail"]
