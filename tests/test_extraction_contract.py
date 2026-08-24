import json
import pytest
from pydantic import ValidationError
from guardianlens.extraction_contract import parse_extracted_listing, strip_code_fence
from guardianlens.models import ExtractedListing, ReviewUpdate

def test_fence_with_trailing_newline_after_close_is_stripped():
    # This is the shape most providers actually emit: a trailing newline after the closing
    # fence. str.strip("`") (the old implementation) leaves the fence in place here because
    # the very last character isn't a backtick, which breaks json.loads downstream.
    raw = '```json\n{"title": "Sony XM5"}\n```\n'
    cleaned = strip_code_fence(raw)
    assert json.loads(cleaned) == {"title": "Sony XM5"}

def test_fence_without_language_tag():
    raw = '```\n{"a": 1}\n```'
    assert json.loads(strip_code_fence(raw)) == {"a": 1}

def test_fence_with_surrounding_whitespace():
    raw = '  \n```json\n{"a": 1}\n```  \n'
    assert json.loads(strip_code_fence(raw)) == {"a": 1}

def test_no_fence_passes_through_unchanged():
    raw = '{"a": 1}'
    assert strip_code_fence(raw) == raw

def test_bare_backticks_inside_value_are_not_mangled():
    raw = '```json\n{"title": "`Quoted`"}\n```\n'
    assert json.loads(strip_code_fence(raw)) == {"title": "`Quoted`"}

def test_ai_and_review_text_fields_are_bounded():
    with pytest.raises(ValidationError):
        ExtractedListing(description='x' * 20001)
    with pytest.raises(ValidationError):
        ReviewUpdate(title='x' * 1001)
    with pytest.raises(Exception, match='outside the listing contract'):
        parse_extracted_listing(json.dumps({'description':'x' * 20001}))

@pytest.mark.parametrize('payload', [
    {'price': -1},
    {'price': float('nan')},
    {'seller_rating': 5.1},
    {'review_count': 100_000_001},
])
def test_ai_numeric_fields_reject_invalid_or_unbounded_values(payload):
    with pytest.raises(Exception, match='outside the listing contract'):
        parse_extracted_listing(json.dumps(payload))
