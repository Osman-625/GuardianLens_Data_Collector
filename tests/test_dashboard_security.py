from __future__ import annotations

from datetime import datetime
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from conftest import data_url, image_bytes
from guardianlens.app import app
from guardianlens.capture import create_capture
from guardianlens.dashboard import (
    LISTING_STATUSES,
    _operator_day_utc_bounds,
    dashboard_snapshot,
    records_page,
    review_queue_snapshot,
)
from guardianlens.db import connect, now_iso, transaction
from guardianlens.models import CaptureRequest
from guardianlens.review import action as review_action, begin_review, get_listing
from guardianlens.security import ensure_local_token
from guardianlens.states import transition


def _capture(suffix: str = "700001") -> dict:
    return create_capture(
        CaptureRequest(
            platform="carousell",
            source_url=f"https://www.carousell.com.my/p/dashboard-fixture-{suffix}/",
            page_title="Dashboard fixture",
            screenshot_data_url=data_url(image_bytes()),
            image_urls=["https://images.example.test/product.jpg"],
            listing_evidence={"h1_text": "Dashboard fixture product", "has_price_signal": True},
        )
    )


def _make_ready(listing_id: str) -> None:
    transition(listing_id, "processing", "test")
    transition(listing_id, "ai_extracted", "test")
    transition(listing_id, "validating", "test")
    transition(listing_id, "ready_for_review", "test")


def test_dashboard_snapshot_has_exact_database_counts(isolated):
    first = _capture("700001")
    second = _capture("700002")
    transition(second["listing_id"], "processing", "test")
    transition(second["listing_id"], "ai_failed", "safe test failure")

    snapshot = dashboard_snapshot()
    con = connect()
    try:
        actual = {
            row["status"]: row["n"]
            for row in con.execute("SELECT status, COUNT(*) AS n FROM listings GROUP BY status")
        }
        total = con.execute("SELECT COUNT(*) AS n FROM listings").fetchone()["n"]
    finally:
        con.close()

    assert set(LISTING_STATUSES).issubset(snapshot["counts"])
    assert snapshot["counts"]["captured"] == 1
    assert snapshot["counts"]["ai_failed"] == 1
    assert {key: value for key, value in snapshot["counts"].items() if value} == actual
    assert sum(snapshot["counts"].values()) == total
    assert first["listing_id"] in {row["listing_id"] for row in snapshot["operational_queue"]}
    assert second["listing_id"] in {row["listing_id"] for row in snapshot["operational_queue"]}


def test_dashboard_html_and_live_status_endpoint_agree(isolated):
    _capture("700006")
    with TestClient(app) as client:
        page = client.get("/")
        status = client.get("/api/dashboard/status")

    assert page.status_code == 200
    assert "Exact database status counts" in page.text
    assert "Operational queue and failures" in page.text
    assert status.status_code == 200
    assert status.json()["counts"]["captured"] == 1
    assert status.json()["plan"]["approved_total"] == 0
    assert status.json()["batch"]["control"]["command"] == "idle"


def test_host_boundary_and_security_headers(isolated):
    with TestClient(app) as client:
        rejected = client.get("/health", headers={"Host": "evil.example"})
        assert rejected.status_code == 421

        response = client.get("/health")
        assert response.status_code == 200
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "no-store"


def test_review_get_is_read_only_and_form_mutations_require_token(isolated):
    captured = _capture("700003")
    listing_id = captured["listing_id"]
    _make_ready(listing_id)

    with TestClient(app) as client:
        response = client.get(f"/review/{listing_id}")
        assert response.status_code == 200
        assert get_listing(listing_id)["status"] == "ready_for_review"

        rejected = client.post(f"/review/{listing_id}/begin", follow_redirects=False)
        assert rejected.status_code == 403
        assert get_listing(listing_id)["status"] == "ready_for_review"

        accepted = client.post(
            f"/review/{listing_id}/begin",
            data={"token": ensure_local_token()},
            follow_redirects=False,
        )
        assert accepted.status_code == 303
        assert get_listing(listing_id)["status"] == "under_review"


def test_private_capture_evidence_rejects_cross_site_embedding(isolated):
    captured = _capture("700004")
    listing_id = captured["listing_id"]
    with TestClient(app) as client:
        denied = client.get(
            f"/asset/screenshot/{listing_id}",
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert denied.status_code == 403

        allowed = client.get(f"/asset/screenshot/{listing_id}")
        assert allowed.status_code == 200
        assert allowed.headers["cache-control"] == "no-store, private"


def test_retry_endpoint_is_token_protected_and_audited(isolated):
    captured = _capture("700005")
    listing_id = captured["listing_id"]
    transition(listing_id, "processing", "test")
    transition(listing_id, "ai_failed", "safe test failure")

    with TestClient(app) as client:
        denied = client.post("/api/queue/retry", json={"listing_ids": [listing_id]})
        assert denied.status_code == 401

        accepted = client.post(
            "/api/queue/retry",
            json={"listing_ids": [listing_id]},
            headers={"X-GuardianLens-Token": ensure_local_token()},
        )
        assert accepted.status_code == 200
        assert accepted.json() == {"retried": [listing_id], "rejected": []}
        assert get_listing(listing_id)["status"] == "captured"

    con = connect()
    try:
        history = con.execute(
            "SELECT reason FROM status_history WHERE listing_id=? ORDER BY timestamp DESC LIMIT 1",
            (listing_id,),
        ).fetchone()
    finally:
        con.close()
    assert history["reason"] == "human requested eligible retry"


def test_edit_and_approve_route_rolls_back_edits_when_approval_fails(isolated):
    captured = _capture("700007")
    listing_id = captured["listing_id"]
    _make_ready(listing_id)
    token = ensure_local_token()

    with TestClient(app) as client:
        assert client.post(
            f"/review/{listing_id}/begin",
            data={"token": token},
            follow_redirects=False,
        ).status_code == 303
        failed = client.post(
            f"/review/{listing_id}/save",
            data={
                "token": token,
                "title": "Must roll back",
                "category": "Audio",
                "price": "123.45",
                "currency": "MYR",
                "submit_action": "approve",
            },
            follow_redirects=False,
        )

    assert failed.status_code == 400
    assert 'role="alert"' in failed.text
    assert 'value="Must roll back"' in failed.text
    assert "at least one product image is required" in failed.text
    item = get_listing(listing_id)
    assert item["status"] == "under_review"
    assert item["title"] is None
    con = connect()
    try:
        edit_count = con.execute(
            "SELECT COUNT(*) AS n FROM review_actions WHERE listing_id=? AND action='edit'",
            (listing_id,),
        ).fetchone()["n"]
    finally:
        con.close()
    assert edit_count == 0


def test_review_validation_error_preserves_submitted_fields(isolated):
    captured = _capture("700010")
    listing_id = captured["listing_id"]
    _make_ready(listing_id)
    token = ensure_local_token()
    with TestClient(app) as client:
        client.post(
            f"/review/{listing_id}/begin",
            data={"token": token},
            follow_redirects=False,
        )
        response = client.post(
            f"/review/{listing_id}/save",
            data={
                "token": token,
                "title": "Keep this correction",
                "price": "-1",
                "submit_action": "save",
            },
        )
    assert response.status_code == 400
    assert 'role="alert"' in response.text
    assert 'value="Keep this correction"' in response.text
    assert 'value="-1"' in response.text
    assert get_listing(listing_id)["title"] is None


def test_batch_start_rejects_an_unconfigured_active_provider(isolated, monkeypatch):
    monkeypatch.setattr("guardianlens.app.provider_configured", lambda _provider: False)
    with TestClient(app) as client:
        response = client.post(
            "/api/batch/start",
            json={"duration_minutes": 30, "item_limit": 1},
            headers={"X-GuardianLens-Token": ensure_local_token()},
        )
    assert response.status_code == 409
    assert "no API key configured" in response.json()["detail"]


def test_model_listing_endpoint_never_returns_raw_provider_secrets(isolated, monkeypatch):
    def fail_with_secret():
        raise RuntimeError("upstream failed authorization token=sk-secretfixture123456")

    monkeypatch.setattr("guardianlens.app.provider_configured", lambda _provider: True)
    monkeypatch.setitem(__import__("guardianlens.app", fromlist=["MODEL_LISTERS"]).MODEL_LISTERS, "openai", fail_with_secret)
    with TestClient(app) as client:
        response = client.get(
            "/api/ai/models/openai",
            headers={"X-GuardianLens-Token": ensure_local_token()},
        )
    assert response.status_code == 502
    assert "sk-secretfixture123456" not in response.text
    assert "model listing failed" in response.json()["detail"]


def test_operator_day_uses_kuala_lumpur_midnight(isolated):
    from guardianlens.config import settings

    original = settings.operator_timezone
    object.__setattr__(settings, "operator_timezone", "Asia/Kuala_Lumpur")
    try:
        start, end = _operator_day_utc_bounds(
            datetime(2026, 8, 24, 0, 30, tzinfo=ZoneInfo("Asia/Kuala_Lumpur"))
        )
    finally:
        object.__setattr__(settings, "operator_timezone", original)
    assert start == "2026-08-23T16:00:00+00:00"
    assert end == "2026-08-24T16:00:00+00:00"


def test_records_index_filters_terminal_states_and_escapes_like_wildcards(isolated):
    first = _capture("700008")
    second = _capture("700009")
    _make_ready(second["listing_id"])
    begin_review(second["listing_id"])
    review_action(second["listing_id"], "rejected", "test terminal state")
    with transaction() as con:
        con.execute(
            "UPDATE listings SET page_title=? WHERE listing_id=?",
            ("Literal %_\\ record", first["listing_id"]),
        )

    literal = records_page(query="%_\\")
    assert [row["listing_id"] for row in literal["rows"]] == [first["listing_id"]]
    assert records_page(status="rejected")["total"] == 1
    assert records_page(platform="mudah")["total"] == 0
    assert records_page(query="no such record")["rows"] == []

    with TestClient(app) as client:
        terminal = client.get("/records", params={"status": "rejected", "page": 999})
        searched = client.get("/records", params={"q": "%_\\"})
        invalid = client.get("/records", params={"status": "not-a-status"})
    assert terminal.status_code == 200
    assert f"/records/{second['listing_id']}" in terminal.text
    assert f"/records/{first['listing_id']}" not in terminal.text
    assert "Page 1 of 1" in terminal.text
    assert searched.status_code == 200
    assert f"/records/{first['listing_id']}" in searched.text
    assert invalid.status_code == 400


def test_review_queue_is_bounded_but_reports_full_total(isolated):
    timestamp = now_iso()
    with transaction() as con:
        for index in range(105):
            listing_id = str(uuid.uuid4())
            con.execute(
                """INSERT INTO listings(
                       listing_id,platform,source_url,source_url_hash,status,
                       captured_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    listing_id,
                    "carousell",
                    f"https://www.carousell.com.my/p/queue-{index}-{800000 + index}/",
                    listing_id,
                    "ready_for_review",
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
    snapshot = review_queue_snapshot()
    assert snapshot["total"] == 105
    assert snapshot["limit"] == 100
    assert len(snapshot["rows"]) == 100
    with TestClient(app) as client:
        response = client.get("/api/review/queue")
    assert response.status_code == 200
    assert response.json()["total"] == 105
    assert len(response.json()["rows"]) == 100


def test_dashboard_ui_contract_guards_async_provider_state_and_explains_missing_images():
    root = Path(__file__).resolve().parents[1]
    javascript = (root / "src/guardianlens/static/app.js").read_text(encoding="utf-8")
    dashboard = (root / "src/guardianlens/templates/dashboard.html").read_text(encoding="utf-8")
    record_detail = (root / "src/guardianlens/templates/record_detail.html").read_text(encoding="utf-8")

    assert "let modelRequestGeneration = 0;" in javascript
    assert "requestGeneration !== modelRequestGeneration || currentProvider !== active" in javascript
    assert "const previousDisabledStates = providerButtons.map((button) => button.disabled);" in javascript
    assert "button.disabled = true;" in javascript
    assert "button.disabled = previousDisabledStates[index];" in javascript
    assert 'aria-pressed="{% if provider.active %}true{% else %}false{% endif %}"' in dashboard
    assert "item.status in ['ready_for_review', 'under_review']" in record_detail
    assert "This approved record no longer has complete retained image evidence" in record_detail


def test_private_asset_route_rejects_a_directory_path(isolated):
    from guardianlens.config import settings

    captured = _capture("700011")
    directory = settings.data_dir / "private" / "screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    with transaction() as con:
        con.execute(
            "UPDATE listings SET screenshot_path=? WHERE listing_id=?",
            (directory.relative_to(settings.root).as_posix(), captured["listing_id"]),
        )

    with TestClient(app) as client:
        response = client.get(f"/asset/screenshot/{captured['listing_id']}")
    assert response.status_code == 404
