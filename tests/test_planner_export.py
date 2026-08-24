import csv, json, uuid
from types import SimpleNamespace
import pytest
import guardianlens.exports as exports_module
import guardianlens.planner as planner_module
from guardianlens.db import transaction, now_iso, connect
from guardianlens.planner import planner_snapshot
from guardianlens.exports import export_approved

def seed(con,platform,category,status='approved',idx=0):
    lid=str(uuid.uuid4());n=now_iso();con.execute("INSERT INTO listings(listing_id,platform,source_url,source_url_hash,title,category,price,currency,status,captured_at,approved_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(lid,platform,f'https://{platform}.test/{lid}',lid,'Item',category,10,'MYR',status,n,n if status=='approved' else None,n,n));return lid

def test_planner_uses_1500_1000_targets(isolated):
    with transaction() as con:
        for i in range(3): seed(con,'carousell','Phones',idx=i)
        for i in range(2): seed(con,'mudah','Cameras',idx=i)
    p=planner_snapshot();assert p['total_target']==2500;assert p['platforms']['carousell']['target']==1500;assert p['platforms']['mudah']['target']==1000
    assert p['approved_total']==5 and len(p['recommendations'])==3

def test_export_has_no_private_screenshot_fields(isolated):
    # Private evidence (screenshot_path, staging_text_path) must never leave the collector.
    # AI provenance and the human review/audit trail are intentionally in scope per
    # docs/DATA_BOUNDARY.md's "Collection data" definition, so those fields belong in the export.
    with transaction() as con: seed(con,'carousell','Audio')
    csv_path,img_path=export_approved(); header=next(csv.reader(csv_path.open(encoding='utf-8-sig')))
    assert 'screenshot_path' not in header and 'staging_text_path' not in header
    assert 'ai_provider' in header and 'ai_extraction_timestamp' in header
    assert 'human_corrections_json' in header and 'status_history_json' in header
    assert img_path.exists()


def test_planner_omits_filled_targets_and_keeps_pending_counts_traceable(isolated, monkeypatch):
    fake_settings = SimpleNamespace(
        platform_targets=lambda: {
            'total_target': 5,
            'platforms': {
                'carousell': {'target': 2, 'ratio': .5},
                'mudah': {'target': 3, 'ratio': .5},
            },
        },
        categories=lambda: [
            {'name': 'Phones', 'target_pct': 60},
            {'name': 'Audio', 'target_pct': 40},
        ],
    )
    monkeypatch.setattr(planner_module, 'settings', fake_settings)
    with transaction() as con:
        seed(con, 'carousell', 'Phones')
        seed(con, 'carousell', 'Audio')
        seed(con, 'mudah', None, status='captured')
        seed(con, 'mudah', 'Phones', status='under_review')
        seed(con, 'mudah', 'Audio', status='ai_failed')

    con = connect()
    try:
        snapshot = planner_snapshot(con)
    finally:
        con.close()

    assert snapshot['approved_total'] == 2
    assert snapshot['pending_total'] == 2
    assert snapshot['unassigned_category_pending'] == 1
    assert snapshot['platforms']['carousell']['remaining'] == 0
    assert snapshot['platforms']['mudah']['pending'] == 2
    assert snapshot['recommendations']
    assert {item['platform'] for item in snapshot['recommendations']} == {'mudah'}
    assert all(item['remaining'] > 0 for item in snapshot['recommendations'])


def test_category_rounding_sums_exactly_to_total_target(isolated, monkeypatch):
    fake_settings = SimpleNamespace(
        platform_targets=lambda: {
            'total_target': 3,
            'platforms': {
                'carousell': {'target': 2, 'ratio': 2 / 3},
                'mudah': {'target': 1, 'ratio': 1 / 3},
            },
        },
        categories=lambda: [
            {'name': 'Phones', 'target_pct': 50},
            {'name': 'Audio', 'target_pct': 50},
        ],
    )
    monkeypatch.setattr(planner_module, 'settings', fake_settings)
    con = connect()
    try:
        snapshot = planner_snapshot(con)
    finally:
        con.close()
    assert sum(item['target'] for item in snapshot['categories']) == 3


def test_recommendations_split_one_category_shortage_across_platform_budgets(isolated, monkeypatch):
    fake_settings = SimpleNamespace(
        platform_targets=lambda: {
            'total_target': 4,
            'platforms': {
                'carousell': {'target': 2, 'ratio': .5},
                'mudah': {'target': 2, 'ratio': .5},
            },
        },
        categories=lambda: [{'name': 'Phones', 'target_pct': 100}],
    )
    monkeypatch.setattr(planner_module, 'settings', fake_settings)

    snapshot = planner_snapshot()

    assert len(snapshot['recommendations']) == 2
    assert {item['platform'] for item in snapshot['recommendations']} == {'carousell', 'mudah'}
    assert sum(item['remaining'] for item in snapshot['recommendations']) == 4


def test_planner_counts_blank_pending_category_as_unassigned(isolated, monkeypatch):
    fake_settings = SimpleNamespace(
        platform_targets=lambda: {
            'total_target': 1,
            'platforms': {'carousell': {'target': 1, 'ratio': 1.0}},
        },
        categories=lambda: [{'name': 'Phones', 'target_pct': 100}],
    )
    monkeypatch.setattr(planner_module, 'settings', fake_settings)
    with transaction() as con:
        seed(con, 'carousell', '', status='captured')

    snapshot = planner_snapshot()

    assert snapshot['pending_total'] == 1
    assert snapshot['unassigned_category_pending'] == 1
    assert snapshot['categories'][0]['pending'] == 0


def test_planner_rejects_negative_allocation_configuration(isolated, monkeypatch):
    fake_settings = SimpleNamespace(
        platform_targets=lambda: {
            'total_target': 5,
            'platforms': {
                'carousell': {'target': -1, 'ratio': .5},
                'mudah': {'target': 6, 'ratio': .5},
            },
        },
        categories=lambda: [{'name': 'Phones', 'target_pct': 100}],
    )
    monkeypatch.setattr(planner_module, 'settings', fake_settings)

    with pytest.raises(ValueError, match='cannot be negative'):
        planner_snapshot()


def test_export_is_approved_only_includes_audit_provenance_and_neutralizes_formulas(isolated):
    with transaction() as con:
        approved_id = seed(con, 'carousell', 'Audio')
        seed(con, 'mudah', 'Audio', status='rejected')
        con.execute(
            "UPDATE listings SET title=?,description=?,ai_extracted=1,ai_provider='openai',ai_model='fixture-model',ai_prompt_version='collector_v1' WHERE listing_id=?",
            ('  =HYPERLINK("https://example.test")', '\t+dangerous spreadsheet text', approved_id),
        )
        extracted_at = now_iso()
        con.execute(
            "INSERT INTO ai_extractions(extraction_id,listing_id,provider,model,prompt_version,created_at,status) VALUES(?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), approved_id, 'openai', 'fixture-model', 'collector_v1', extracted_at, 'success'),
        )
        con.execute(
            "INSERT INTO review_actions(review_action_id,listing_id,action,previous_data,updated_data,created_at) VALUES(?,?,?,?,?,?)",
            (str(uuid.uuid4()), approved_id, 'edit', json.dumps({'title': 'AI title'}), json.dumps({'title': 'Human title'}), now_iso()),
        )
        con.execute(
            "INSERT INTO status_history(history_id,listing_id,from_status,to_status,timestamp,reason) VALUES(?,?,?,?,?,?)",
            (str(uuid.uuid4()), approved_id, 'under_review', 'approved', now_iso(), 'human approved'),
        )

    csv_path, _ = export_approved()
    rows = list(csv.DictReader(csv_path.open(encoding='utf-8-sig')))

    assert len(rows) == 1 and rows[0]['listing_id'] == approved_id
    assert rows[0]['title'].startswith("'  =")
    assert rows[0]['description'].startswith("'\t+")
    assert rows[0]['ai_provider'] == 'openai'
    assert rows[0]['ai_extraction_timestamp'] == extracted_at
    corrections = json.loads(rows[0]['human_corrections_json'])
    assert corrections[0]['previous']['title'] == 'AI title'
    assert json.loads(rows[0]['status_history_json'])[-1]['to_status'] == 'approved'


def test_export_pair_leaves_no_partial_files_when_atomic_replace_fails(isolated, monkeypatch):
    with transaction() as con:
        seed(con, 'carousell', 'Audio')

    def fail_replace(_source, _destination):
        raise OSError('simulated atomic replace failure')

    monkeypatch.setattr(exports_module.os, 'replace', fail_replace)
    with pytest.raises(OSError, match='atomic replace'):
        export_approved()

    export_dir = isolated / 'data' / 'exports'
    assert not list(export_dir.glob('approved_listings_*'))
    assert not list(export_dir.glob('approved_images_*'))
    assert not list(export_dir.glob('.*.tmp'))


def test_export_pair_removes_first_file_when_second_atomic_replace_fails(isolated, monkeypatch):
    with transaction() as con:
        seed(con, 'carousell', 'Audio')
    real_replace = exports_module.os.replace
    calls = {'count': 0}

    def fail_second_replace(source, destination):
        calls['count'] += 1
        if calls['count'] == 2:
            raise OSError('simulated second atomic replace failure')
        return real_replace(source, destination)

    monkeypatch.setattr(exports_module.os, 'replace', fail_second_replace)
    with pytest.raises(OSError, match='second atomic replace'):
        export_approved()

    export_dir = isolated / 'data' / 'exports'
    assert not list(export_dir.glob('approved_listings_*'))
    assert not list(export_dir.glob('approved_images_*'))
    assert not list(export_dir.glob('.*.tmp'))
