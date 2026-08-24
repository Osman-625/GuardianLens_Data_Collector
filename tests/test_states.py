import uuid
import pytest
from guardianlens.db import transaction, now_iso
from guardianlens.states import transition, can_transition

def seed(con,status='captured'):
    lid=str(uuid.uuid4()); now=now_iso()
    con.execute("INSERT INTO listings(listing_id,platform,source_url,source_url_hash,status,captured_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(lid,'carousell','https://www.carousell.com.my/p/x','h'+lid,status,now,now,now)); return lid

def test_state_machine_valid_and_invalid(isolated):
    with transaction() as con: lid=seed(con)
    assert can_transition('captured','processing')
    transition(lid,'processing')
    with pytest.raises(ValueError): transition(lid,'approved')


def test_ready_listing_cannot_bypass_under_review(isolated):
    with transaction() as con:
        lid = seed(con)
    transition(lid, 'processing')
    transition(lid, 'ai_extracted')
    transition(lid, 'validating')
    transition(lid, 'ready_for_review')

    with pytest.raises(ValueError):
        transition(lid, 'approved')
