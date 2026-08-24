from __future__ import annotations
from contextlib import asynccontextmanager
from urllib.parse import urlencode
import uvicorn
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError
from .config import settings, AI_PROVIDERS, get_active_provider, set_active_provider, provider_model, provider_configured
from .provider_info import MODEL_LISTERS
from .db import init_db, connect, transaction
from .security import (
    ensure_local_token,
    enforce_local_request,
    get_local_token,
    require_local_token,
    require_local_token_form,
    require_same_origin_asset,
    assert_safe_bind,
)
from .models import CaptureRequest, ReviewUpdate, BatchStartRequest
from .capture import create_capture
from .dashboard import (
    LISTING_STATUSES,
    RETRYABLE_FAILURE_STATUSES,
    REVIEW_QUEUE_STATUSES,
    dashboard_snapshot,
    records_page,
    review_queue_snapshot,
)
from .batch import batch_manager
from .review import (
    PrivateAssetCleanupError,
    get_listing,
    begin_review,
    edit_listing,
    edit_and_approve,
    action as review_action,
)
from .exports import export_approved
from .states import transition
from .errors import classify_error

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    ensure_local_token()
    batch_manager.recover()
    yield
    batch_manager.stop()

app = FastAPI(title="GuardianLens Data Collector", version="2.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=settings.root / "src" / "guardianlens" / "static"), name="static")
templates = Jinja2Templates(directory=settings.root / "src" / "guardianlens" / "templates")

@app.middleware("http")
async def local_security_boundary(request: Request, call_next):
    try:
        enforce_local_request(request)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'"
    )
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if not request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response

@app.get("/health")
def health():
    return {"status":"ok","mode":settings.mode,"host":settings.host,"port":settings.port}

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    snapshot = dashboard_snapshot()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            **snapshot,
            "batch": batch_manager.status(),
            "token": get_local_token(),
            "mode": settings.mode,
            "ai_status": _ai_status_payload(),
        },
    )

@app.get("/api/dashboard/status")
def dashboard_status():
    return {**dashboard_snapshot(), "batch": batch_manager.status(), "ai_status": _ai_status_payload()}

@app.post("/api/capture", dependencies=[Depends(require_local_token)])
def capture(payload: CaptureRequest):
    try:
        return create_capture(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))

@app.get("/review", response_class=HTMLResponse)
def review_queue(request: Request):
    queue = review_queue_snapshot()
    return templates.TemplateResponse(
        request,
        "review_queue.html",
        {**queue, "token": get_local_token()},
    )

@app.get("/api/review/queue")
def review_queue_status():
    return review_queue_snapshot()


def _review_response(
    request: Request,
    item: dict,
    *,
    submitted: dict | None = None,
    error_message: str | None = None,
    status_code: int = 200,
):
    display_item = {**item, **(submitted or {})}
    return templates.TemplateResponse(
        request,
        "review_detail.html",
        {
            "item": display_item,
            "categories": [x["name"] for x in settings.categories()],
            "token": get_local_token(),
            "review_active": item["status"] == "under_review",
            "error_message": error_message,
        },
        status_code=status_code,
    )


def _record_response(
    request: Request,
    item: dict,
    *,
    page_notice: str | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "record_detail.html",
        {
            "item": item,
            "token": get_local_token(),
            "reviewable": item["status"] in REVIEW_QUEUE_STATUSES,
            "page_notice": page_notice,
        },
        status_code=status_code,
    )


def _validation_message(error: ValidationError) -> str:
    details = []
    for item in error.errors(include_input=False, include_url=False):
        field = ".".join(str(part) for part in item.get("loc", ())) or "field"
        details.append(f"{field}: {item.get('msg', 'invalid value')}")
    return "Check the submitted values. " + "; ".join(details[:6])


@app.get("/review/{listing_id}", response_class=HTMLResponse)
def review_detail(request: Request, listing_id: str):
    try:
        item = get_listing(listing_id)
    except (KeyError, ValueError) as e:
        raise HTTPException(404, str(e))
    if item["status"] not in REVIEW_QUEUE_STATUSES:
        raise HTTPException(409, "listing is not in the human review queue")
    return _review_response(request, item)

@app.post("/review/{listing_id}/begin", dependencies=[Depends(require_local_token_form)])
def review_begin(request: Request, listing_id: str):
    try:
        begin_review(listing_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        try:
            item = get_listing(listing_id)
        except KeyError:
            raise HTTPException(404, "listing not found")
        return _record_response(
            request,
            item,
            page_notice=f"Could not begin review: {exc}",
            status_code=409,
        )
    return RedirectResponse(f"/review/{listing_id}", status_code=303)

@app.post("/review/{listing_id}/save", dependencies=[Depends(require_local_token_form)])
async def review_save(request: Request, listing_id: str):
    form = await request.form()
    submitted = {k: v for k, v in form.items() if k in ReviewUpdate.model_fields}
    non_empty = {k: v for k, v in submitted.items() if v != ""}
    try:
        validated = ReviewUpdate.model_validate(non_empty).model_dump(exclude_unset=True)
    except ValidationError as e:
        try:
            item = get_listing(listing_id)
        except KeyError:
            raise HTTPException(404, "listing not found")
        return _review_response(
            request,
            item,
            submitted=submitted,
            error_message=_validation_message(e),
            status_code=400,
        )
    # A field submitted empty is an explicit clear (-> NULL); one omitted from the form is untouched.
    data = {k: validated.get(k) for k in submitted}
    try:
        if form.get("submit_action") == "approve":
            edit_and_approve(listing_id, data)
            return RedirectResponse("/review", status_code=303)
        edit_listing(listing_id, data)
    except ValueError as e:
        try:
            item = get_listing(listing_id)
        except KeyError:
            raise HTTPException(404, "listing not found")
        return _review_response(
            request,
            item,
            submitted=submitted,
            error_message=str(e),
            status_code=400,
        )
    except PrivateAssetCleanupError as e:
        return _record_response(
            request,
            get_listing(listing_id),
            page_notice=str(e),
            status_code=500,
        )
    return RedirectResponse(f"/review/{listing_id}", status_code=303)

@app.post("/review/{listing_id}/action/{target}", dependencies=[Depends(require_local_token_form)])
def act(request: Request, listing_id: str, target: str):
    try:
        review_action(listing_id,target)
    except ValueError as e:
        try:
            item = get_listing(listing_id)
        except KeyError:
            raise HTTPException(404, "listing not found")
        if item["status"] in REVIEW_QUEUE_STATUSES:
            return _review_response(request, item, error_message=str(e), status_code=400)
        return _record_response(request, item, page_notice=str(e), status_code=400)
    except PrivateAssetCleanupError as e:
        return _record_response(
            request,
            get_listing(listing_id),
            page_notice=str(e),
            status_code=500,
        )
    return RedirectResponse("/review", status_code=303)

@app.get("/records", response_class=HTMLResponse)
def record_index(
    request: Request,
    status: str | None = None,
    platform: str | None = None,
    q: str | None = None,
    page: int = 1,
):
    try:
        result = records_page(status=status, platform=platform, query=q, page=page)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    def page_url(target_page: int) -> str:
        values = {
            key: value
            for key, value in {
                "status": result["filters"]["status"],
                "platform": result["filters"]["platform"],
                "q": result["filters"]["query"],
                "page": target_page,
            }.items()
            if value not in {"", None}
        }
        return f"/records?{urlencode(values)}"

    return templates.TemplateResponse(
        request,
        "records.html",
        {
            **result,
            "statuses": LISTING_STATUSES,
            "previous_url": page_url(result["page"] - 1) if result["page"] > 1 else None,
            "next_url": page_url(result["page"] + 1) if result["page"] < result["page_count"] else None,
        },
    )

@app.get("/records/{listing_id}", response_class=HTMLResponse)
def record_detail(request: Request, listing_id: str):
    try:
        item = get_listing(listing_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    return _record_response(request, item)

@app.get("/asset/screenshot/{listing_id}")
def screenshot(request: Request, listing_id: str):
    require_same_origin_asset(request)
    c=connect(); row=c.execute("SELECT screenshot_path FROM listings WHERE listing_id=?",(listing_id,)).fetchone(); c.close()
    if not row or not row["screenshot_path"]: raise HTTPException(404)
    p=(settings.root/row["screenshot_path"]).resolve()
    allowed=(settings.data_dir/"private"/"screenshots").resolve()
    if allowed not in p.parents or not p.is_file(): raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control":"no-store, private","X-Content-Type-Options":"nosniff"})

@app.get("/asset/image/{image_id}")
def image(request: Request, image_id: str):
    require_same_origin_asset(request)
    c=connect(); row=c.execute("SELECT storage_path FROM images WHERE image_id=?",(image_id,)).fetchone(); c.close()
    if not row: raise HTTPException(404)
    p=(settings.root/row["storage_path"]).resolve(); allowed=(settings.data_dir/"images").resolve()
    if allowed not in p.parents or not p.is_file(): raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})

@app.post("/api/batch/start", dependencies=[Depends(require_local_token)])
def batch_start(payload: BatchStartRequest):
    active_provider = get_active_provider()
    if not provider_configured(active_provider):
        raise HTTPException(
            409,
            f"{active_provider} is active but has no API key configured; configure or switch providers before starting",
        )
    try:
        rid = batch_manager.start(payload.duration_minutes, payload.item_limit)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"run_id":rid,"status":"running"}

@app.post("/api/batch/{command}", dependencies=[Depends(require_local_token)])
def batch_command(command: str):
    try:
        if command=="pause": batch_manager.pause()
        elif command=="resume": batch_manager.resume()
        elif command=="stop": batch_manager.stop()
        else: raise HTTPException(404,"unknown batch command")
    except RuntimeError as e: raise HTTPException(409,str(e))
    return batch_manager.status()

@app.get("/api/batch/status")
def batch_status(): return batch_manager.status()

class RetryListingsRequest(BaseModel):
    listing_ids: list[str]

@app.post("/api/queue/retry", dependencies=[Depends(require_local_token)])
def retry_listings(payload: RetryListingsRequest):
    listing_ids = list(dict.fromkeys(payload.listing_ids))
    if not listing_ids or len(listing_ids) > 100:
        raise HTTPException(400, "choose between 1 and 100 failed listings")
    retried: list[str] = []
    rejected: list[dict] = []
    with transaction() as con:
        for listing_id in listing_ids:
            row = con.execute("SELECT status FROM listings WHERE listing_id=?", (listing_id,)).fetchone()
            if not row:
                rejected.append({"listing_id": listing_id, "reason": "not_found"})
                continue
            if row["status"] not in RETRYABLE_FAILURE_STATUSES:
                rejected.append({"listing_id": listing_id, "reason": f"status_{row['status']}_not_retryable"})
                continue
            transition(listing_id, "captured", "human requested eligible retry", con=con)
            retried.append(listing_id)
    return {"retried": retried, "rejected": rejected}

def _ai_status_payload() -> dict:
    active = get_active_provider()
    return {
        "active": active,
        "providers": [
            {"name": name, "configured": provider_configured(name), "model": provider_model(name), "active": name == active}
            for name in AI_PROVIDERS
        ],
    }

@app.get("/api/ai/status")
def ai_status():
    return _ai_status_payload()

class SetProviderRequest(BaseModel):
    provider: str

@app.post("/api/ai/provider", dependencies=[Depends(require_local_token)])
def ai_set_provider(payload: SetProviderRequest):
    if payload.provider not in AI_PROVIDERS:
        raise HTTPException(400, f"unknown provider: {payload.provider!r}")
    if not provider_configured(payload.provider):
        raise HTTPException(400, f"{payload.provider} has no API key set in .env")
    set_active_provider(payload.provider)
    return _ai_status_payload()

@app.get("/api/ai/models/{provider}", dependencies=[Depends(require_local_token)])
def ai_list_models(provider: str):
    if provider not in AI_PROVIDERS:
        raise HTTPException(404, "unknown provider")
    if not provider_configured(provider):
        raise HTTPException(400, f"{provider} has no API key set in .env")
    try:
        models = MODEL_LISTERS[provider]()
    except Exception as e:
        classification = classify_error(e)
        raise HTTPException(502, f"{provider} model listing failed: {classification.user_message}")
    return {"provider": provider, "configured_model": provider_model(provider), "models": models}

@app.post("/api/export", dependencies=[Depends(require_local_token)])
def export():
    paths = export_approved()
    return {"files": [str(path.relative_to(settings.root)) for path in paths]}

def run():
    assert_safe_bind(settings.host)
    uvicorn.run("guardianlens.app:app", host=settings.host, port=settings.port, reload=False)
