from guardianlens.pii import scrub_text, residual_pii

def test_email_and_phone_removed():
    r=scrub_text('Contact abc@example.com or +60 12-345 6789 for this item')
    assert 'abc@example.com' not in r.text
    assert '+60 12-345 6789' not in r.text
    assert len(r.matches)>=2

def test_price_is_not_phone_false_positive():
    r=scrub_text('Sony headphones RM900 used once')
    assert r.text == 'Sony headphones RM900 used once'
    assert residual_pii(r.text)==[]

def test_residual_detector_finds_generic_number():
    assert 'generic_phone' in residual_pii('call +1 555 222 3333 please')

def test_obfuscated_email_contact_link_handle_and_address_are_scrubbed():
    raw = 'Email seller [at] example [dot] com, @seller.my, wa.me/60123456789, No. 12 Jalan Ampang'
    result = scrub_text(raw)
    assert result.text.count('[REDACTED]') >= 4
    assert {'obfuscated_email','social_handle','contact_link','address'} <= set(result.kinds)
    assert residual_pii(result.text) == []

def test_product_codes_dates_and_normal_locations_are_not_scrubbed():
    raw = 'Sony WH-1000XM5, model A12345, purchased 2026-08-20, pickup in Selangor'
    result = scrub_text(raw)
    assert result.text == raw
    assert result.matches == []

def test_generic_long_number_is_residual_not_silently_scrubbed():
    raw = 'Serial 1234567890 is printed on the box'
    result = scrub_text(raw)
    assert result.text == raw
    assert residual_pii(result.text) == ['generic_phone']
