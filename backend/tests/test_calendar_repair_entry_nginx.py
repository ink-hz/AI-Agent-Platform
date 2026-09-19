from pathlib import Path


def test_calendar_repair_is_an_exact_alias_without_office_redirect():
    config = (Path(__file__).resolve().parents[2] / 'deploy/cloud/agent-domain.nginx.conf').read_text()
    assert config.count('location = /calendar-repair {') == 1
    assert config.count('location = /calendar-repair/ {') == 1
    start = config.index('location = /calendar-repair/ {')
    block = config[start:config.index('\n    }', start)]
    assert 'proxy_pass http://127.0.0.1:8011/office/;' in block
    assert 'return ' not in block
    assert 'proxy_set_header Authorization "";' in block
    assert 'proxy_set_header Forwarded "";' in block
    assert 'location ^~ /calendar-repair' not in config
    assert 'return 308 /calendar-repair/$is_args$args;' in config
