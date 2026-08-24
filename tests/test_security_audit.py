import json
import pytest
from guardianlens.security import ensure_local_token, assert_safe_bind
from guardianlens.audit import security_report, data_integrity_report

def test_local_token_created_and_stable(isolated):
    a=ensure_local_token();b=ensure_local_token();assert a==b and len(a)>30

def test_non_local_bind_rejected():
    with pytest.raises(RuntimeError): assert_safe_bind('0.0.0.0')

def test_security_audit_passes_default_manifest(isolated):
    assert security_report()['pass']

def test_integrity_empty_dataset_passes(isolated):
    assert data_integrity_report()['pass']
