import time, uuid
from datetime import datetime, timedelta, timezone
from guardianlens.batch import BatchManager
from guardianlens.audit import data_integrity_report
from guardianlens.db import transaction, now_iso, connect
from guardianlens.review import begin_review
from guardianlens.states import transition

def seed(con,n=3):
    for _ in range(n):
        lid=str(uuid.uuid4());now=now_iso();con.execute("INSERT INTO listings(listing_id,platform,source_url,source_url_hash,status,captured_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(lid,'carousell','https://www.carousell.com.my/p/'+lid,lid,'captured',now,now,now))

def test_batch_start_pause_resume_stop(isolated, monkeypatch):
    with transaction() as con: seed(con,8)
    def fake_process(lid,extractor=None,run_id=None,**kwargs):
        time.sleep(.05)
        with transaction() as con: con.execute("UPDATE listings SET status='ready_for_review' WHERE listing_id=?",(lid,))
        return {'status':'ready_for_review','listing_id':lid}
    monkeypatch.setattr('guardianlens.batch.process_listing',fake_process)
    m=BatchManager();rid=m.start(5,8,lambda:object());time.sleep(.08);m.pause();assert m.status()['control']['command']=='paused';m.resume();assert m.status()['control']['command']=='running';m.stop()
    final=None
    for _ in range(250):
        st=m.status(); final=st['latest_run']['status']
        if final in {'stopped','completed'}: break
        time.sleep(.02)
    assert final in {'stopped','completed'}

def test_crash_recovery_resets_processing(isolated):
    with transaction() as con:
        lid=str(uuid.uuid4());n=now_iso();con.execute("INSERT INTO listings(listing_id,platform,source_url,source_url_hash,status,captured_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(lid,'mudah','https://www.mudah.my/'+lid,lid,'processing',n,n,n))
    m=BatchManager();m.recover();c=connect();st=c.execute('SELECT status FROM listings WHERE listing_id=?',(lid,)).fetchone()['status'];c.close();assert st=='captured'


def test_crash_recovery_audits_intermediate_states_review_and_run_timing(isolated):
    with transaction() as con:
        seed(con, 4)
        ids = [row["listing_id"] for row in con.execute("SELECT listing_id FROM listings ORDER BY rowid")]
        for listing_id in ids:
            con.execute(
                "INSERT INTO status_history(history_id,listing_id,from_status,to_status,timestamp,reason) VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), listing_id, None, "captured", now_iso(), "fixture capture"),
            )
    processing_id, extracted_id, validating_id, review_id = ids
    transition(processing_id, "processing")
    transition(extracted_id, "processing"); transition(extracted_id, "ai_extracted")
    transition(validating_id, "processing"); transition(validating_id, "ai_extracted"); transition(validating_id, "validating")
    transition(review_id, "processing"); transition(review_id, "ai_extracted"); transition(review_id, "validating"); transition(review_id, "ready_for_review")
    begin_review(review_id)

    started = (datetime.now(timezone.utc) - timedelta(seconds=125)).isoformat()
    with transaction() as con:
        con.execute(
            "INSERT INTO processing_runs(run_id,started_at,status) VALUES('interrupted-run',?,'running')",
            (started,),
        )

    BatchManager().recover()

    con = connect()
    try:
        statuses = {
            row["listing_id"]: row["status"]
            for row in con.execute("SELECT listing_id,status FROM listings")
        }
        assert statuses[processing_id] == "captured"
        assert statuses[extracted_id] == "processing_failed"
        assert statuses[validating_id] == "processing_failed"
        assert statuses[review_id] == "ready_for_review"
        reasons = [
            row["reason"]
            for row in con.execute("SELECT reason FROM status_history WHERE reason LIKE 'crash recovery:%'")
        ]
        assert len(reasons) == 4
        session = con.execute(
            "SELECT action,finished_at,duration_seconds FROM review_actions WHERE listing_id=? AND action='review_session_interrupted'",
            (review_id,),
        ).fetchone()
        assert session["action"] == "review_session_interrupted"
        assert session["finished_at"] is not None
        assert session["duration_seconds"] is None
        run = con.execute("SELECT * FROM processing_runs WHERE run_id='interrupted-run'").fetchone()
        assert run["status"] == "interrupted"
        assert run["finished_at"] is not None
        assert 120 <= run["duration_seconds"] <= 130
    finally:
        con.close()
    integrity = data_integrity_report()
    assert integrity["pass"], integrity
