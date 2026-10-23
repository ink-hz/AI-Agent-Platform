from pathlib import Path

CLOUD = Path(__file__).resolve().parents[2] / 'deploy/cloud'


def test_legacy_fae_has_no_backend_proxy_or_query_reflecting_redirect():
    text = (CLOUD / 'agent-domain.nginx.conf').read_text()
    assert 'proxy_pass http://127.0.0.1:8000;' not in text
    start = text.index('# FAE_ENTRY_RETIREMENT_V1_BEGIN')
    end = text.index('# FAE_ENTRY_RETIREMENT_V1_END')
    retired = text[start:end]
    assert 'if ($request_method ~ ^(GET|HEAD)$)' in retired
    assert 'return 308 https://fae.orbbec.com.cn/;' in retired
    assert 'fae_entry_retired' in retired
    assert '$args' not in retired and '$request_uri' not in retired
    assert 'location ^~ /fae/api/ {' in retired
    assert 'location ^~ /fae/assets/ {' in retired
    assert 'location ^~ /fae/manage/ {' in text[end:]


def test_acceptance_consumes_canonical_fae_session_and_history():
    text = (CLOUD / 'accept.sh').read_text()
    assert '"$base/fae/api/' not in text
    assert 'https://fae.orbbec.com.cn/enterprise/session' in text
    assert 'https://fae.orbbec.com.cn/authenticated/conversations' in text
    assert "-H 'Origin: https://fae.orbbec.com.cn'" in text
    assert '"location ^~ /fae/ {": "http://127.0.0.1:8000"' not in text
    assert 'fae_entry_retired' in text


def test_office_fae_guard_pins_retired_ip_redirect():
    for script in ('publish-office-path-migration.sh', 'rollback-office-path-migration.sh'):
        text = (CLOUD / script).read_text()
        assert '"$fae_ip_http" == "308 https://fae.orbbec.com.cn/"' in text
        assert '"$fae_ip_http" =~ ^2' not in text
