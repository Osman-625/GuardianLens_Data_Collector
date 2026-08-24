from __future__ import annotations
import threading
import time
import uuid
from datetime import datetime, timezone
from .db import connect, transaction, now_iso
from .processor import process_listing
from .errors import classify_error
from .states import transition

# A misconfigured provider (bad model name, malformed prompt schema, provider outage) fails every
# item identically but isn't an auth/quota error, so it wouldn't otherwise halt the run. Without this,
# a systemic error burns through the whole captured queue one API call at a time before anyone notices.
CONSECUTIVE_FAILURE_LIMIT = 5

class BatchManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._run_id: str | None = None

    def recover(self) -> None:
        recovered_at = datetime.now(timezone.utc)
        recovered_iso = recovered_at.isoformat()

        def elapsed_seconds(started_at: str | None) -> int:
            if not started_at:
                return 0
            try:
                started = datetime.fromisoformat(started_at)
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                return max(0, int((recovered_at - started).total_seconds()))
            except (TypeError, ValueError):
                return 0

        with transaction() as con:
            processing = con.execute("SELECT listing_id FROM listings WHERE status='processing'").fetchall()
            for row in processing:
                transition(
                    row["listing_id"],
                    "captured",
                    "crash recovery: processing had not reached a durable extraction state",
                    con=con,
                )

            interrupted = con.execute(
                "SELECT listing_id,status FROM listings WHERE status IN ('ai_extracted','validating')"
            ).fetchall()
            for row in interrupted:
                transition(
                    row["listing_id"],
                    "processing_failed",
                    f"crash recovery: interrupted while {row['status']}",
                    con=con,
                )

            reviews = con.execute("SELECT listing_id FROM listings WHERE status='under_review'").fetchall()
            valid_return_states = {"ready_for_review", "needs_attention", "skipped", "flagged"}
            for row in reviews:
                listing_id = row["listing_id"]
                prior = con.execute(
                    """
                    SELECT from_status
                    FROM status_history
                    WHERE listing_id=? AND to_status='under_review'
                    ORDER BY timestamp DESC, rowid DESC
                    LIMIT 1
                    """,
                    (listing_id,),
                ).fetchone()
                target = prior["from_status"] if prior and prior["from_status"] in valid_return_states else "needs_attention"
                sessions = con.execute(
                    """
                    SELECT review_action_id,started_at
                    FROM review_actions
                    WHERE listing_id=? AND action='review_session' AND finished_at IS NULL
                    """,
                    (listing_id,),
                ).fetchall()
                for session in sessions:
                    con.execute(
                        """UPDATE review_actions
                           SET action='review_session_interrupted',finished_at=?,duration_seconds=NULL
                           WHERE review_action_id=?""",
                        (recovered_iso, session["review_action_id"]),
                    )
                transition(
                    listing_id,
                    target,
                    f"crash recovery: interrupted human review restored to {target}",
                    con=con,
                )

            runs = con.execute(
                "SELECT run_id,started_at FROM processing_runs WHERE status IN ('running','paused','stopping')"
            ).fetchall()
            for run in runs:
                con.execute(
                    "UPDATE processing_runs SET status='interrupted',finished_at=?,duration_seconds=? WHERE run_id=?",
                    (recovered_iso, elapsed_seconds(run["started_at"]), run["run_id"]),
                )
            con.execute(
                "UPDATE processor_control SET command='idle',updated_at=? WHERE control_id=1",
                (recovered_iso,),
            )

    def start(self, duration_minutes: int | None, item_limit: int | None, extractor_factory=None) -> str:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("a batch run is already active")
            self._stop.clear(); self._pause.clear()
            run_id = str(uuid.uuid4()); self._run_id = run_id
            with transaction() as con:
                con.execute("INSERT INTO processing_runs(run_id,started_at,duration_limit_minutes,item_limit,status) VALUES(?,?,?,?,?)", (run_id,now_iso(),duration_minutes,item_limit,"running"))
                con.execute("UPDATE processor_control SET command='running',updated_at=? WHERE control_id=1", (now_iso(),))
            self._thread = threading.Thread(target=self._run, args=(run_id,duration_minutes,item_limit,extractor_factory), daemon=True, name="guardianlens-batch")
            self._thread.start()
            return run_id

    def _run(self, run_id: str, duration_minutes: int | None, item_limit: int | None, extractor_factory) -> None:
        started = time.monotonic(); attempted = successful = duplicates = ai_failures = image_failures = privacy_warnings = needs_attention = other_failures = failed = 0
        halt_reason: str | None = None
        consecutive_failures = 0
        try:
            while True:
                if self._stop.is_set(): break
                while self._pause.is_set() and not self._stop.is_set(): time.sleep(0.2)
                if self._stop.is_set(): break
                if duration_minutes and time.monotonic() - started >= duration_minutes * 60: break
                if item_limit and attempted >= item_limit: break
                c = connect()
                row = c.execute(
                    "SELECT listing_id FROM listings WHERE status='captured' ORDER BY captured_at LIMIT 1",
                ).fetchone()
                c.close()
                if not row: break
                attempted += 1
                try:
                    result = process_listing(
                        row["listing_id"],
                        run_id=run_id,
                        extractor_factory=extractor_factory,
                        stop_event=self._stop,
                    )
                    consecutive_failures = 0
                    if result["status"] == "duplicate_blocked":
                        duplicates += 1
                    else:
                        successful += 1
                        if result["status"] == "needs_attention":
                            needs_attention += 1
                            cx=connect(); pii_n=cx.execute("SELECT COUNT(*) n FROM warnings WHERE listing_id=? AND warning_type='pii'",(row["listing_id"],)).fetchone()["n"]; cx.close()
                            if pii_n: privacy_warnings += 1
                except Exception as e:
                    c = connect(); st = c.execute("SELECT status FROM listings WHERE listing_id=?", (row["listing_id"],)).fetchone(); c.close()
                    status = st["status"] if st else ""
                    if status == "ai_failed": ai_failures += 1
                    elif status == "image_failed": image_failures += 1
                    else: other_failures += 1
                    failed += 1
                    consecutive_failures += 1
                    classification = classify_error(e)
                    if classification.halt_batch:
                        halt_reason = classification.kind
                    elif consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                        halt_reason = "repeated_failures"
                with transaction() as con:
                    con.execute("UPDATE processing_runs SET attempted=?,processed=?,successful=?,duplicates=?,ai_failures=?,image_failures=?,privacy_warnings=?,needs_attention=?,other_failures=?,failed=? WHERE run_id=?", (attempted,attempted,successful,duplicates,ai_failures,image_failures,privacy_warnings,needs_attention,other_failures,failed,run_id))
                if halt_reason:
                    break
        finally:
            if self._stop.is_set():
                final_status = "stopped"
            elif halt_reason:
                final_status = f"halted_{halt_reason}_error"
            else:
                final_status = "completed"
            duration_seconds = max(0, int(time.monotonic() - started))
            with transaction() as con:
                con.execute("UPDATE processing_runs SET finished_at=?,duration_seconds=?,status=?,attempted=?,processed=?,successful=?,duplicates=?,ai_failures=?,image_failures=?,privacy_warnings=?,needs_attention=?,other_failures=?,failed=? WHERE run_id=?", (now_iso(),duration_seconds,final_status,attempted,attempted,successful,duplicates,ai_failures,image_failures,privacy_warnings,needs_attention,other_failures,failed,run_id))
                con.execute("UPDATE processor_control SET command='idle',updated_at=? WHERE control_id=1", (now_iso(),))

    def pause(self) -> None:
        if not self._thread or not self._thread.is_alive(): raise RuntimeError("no active batch run")
        self._pause.set()
        with transaction() as con:
            con.execute("UPDATE processor_control SET command='paused',updated_at=? WHERE control_id=1", (now_iso(),))
            if self._run_id: con.execute("UPDATE processing_runs SET status='paused' WHERE run_id=?", (self._run_id,))

    def resume(self) -> None:
        if not self._thread or not self._thread.is_alive(): raise RuntimeError("no active batch run")
        self._pause.clear()
        with transaction() as con:
            con.execute("UPDATE processor_control SET command='running',updated_at=? WHERE control_id=1", (now_iso(),))
            if self._run_id: con.execute("UPDATE processing_runs SET status='running' WHERE run_id=?", (self._run_id,))

    def stop(self) -> None:
        if not self._thread or not self._thread.is_alive(): return
        self._stop.set(); self._pause.clear()
        with transaction() as con:
            con.execute("UPDATE processor_control SET command='stopping',updated_at=? WHERE control_id=1", (now_iso(),))
            if self._run_id: con.execute("UPDATE processing_runs SET status='stopping' WHERE run_id=?", (self._run_id,))
        # Give a short grace window for a fast current item to finish and commit its final run state.
        # Slow network/API work is not force-killed; status remains `stopping` until the worker exits.
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    def status(self) -> dict:
        c = connect()
        ctrl = dict(c.execute("SELECT * FROM processor_control WHERE control_id=1").fetchone())
        run = c.execute("SELECT * FROM processing_runs ORDER BY started_at DESC LIMIT 1").fetchone(); c.close()
        return {"control": ctrl, "latest_run": dict(run) if run else None, "thread_alive": bool(self._thread and self._thread.is_alive())}

batch_manager = BatchManager()
