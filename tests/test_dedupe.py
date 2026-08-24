from guardianlens.dedupe import canonicalize_url,url_hash,metadata_signature,hamming_hex

def test_canonical_url_removes_tracking_and_fragment():
    u='https://www.carousell.com.my/p/123/?utm_source=x&b=2#a'
    assert canonicalize_url(u)=='https://www.carousell.com.my/p/123?b=2'

def test_url_hash_stable():
    assert url_hash('https://mudah.my/a?utm_source=x')==url_hash('https://mudah.my/a')

def test_metadata_signature_normalizes_spacing_case():
    a=metadata_signature('carousell','Sony   XM5','Nice Item',900)
    b=metadata_signature('carousell','sony xm5','nice-item',900.0)
    assert a==b

def test_hamming_hex():
    assert hamming_hex('0f','0e')==1
