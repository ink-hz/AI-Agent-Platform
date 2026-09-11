import pytest
from app.hr.research_library import ResearchLibrary
from app.hr.panorama_repository import PanoramaNotFound
from app.hr.source_library import SourceLibrary


def test_research_source_linking_uses_original_archive_without_changing_report(monkeypatch):
    seen = []
    def resolve(self, markdown, source_bundle_id):
        seen.append(source_bundle_id)
        return {'edition':'fixed-source','source_bundle_id':source_bundle_id,'links':{}}
    monkeypatch.setattr(SourceLibrary, 'resolve_references', resolve, raising=False)
    library = ResearchLibrary(); catalog = library.catalog()
    doc = library.document(catalog['articles'][0]['id'], catalog['edition'])
    assert doc['source_reference']['edition'] == 'fixed-source'
    assert seen == [catalog['source_archive_id']]
    assert doc['sha256'] == catalog['articles'][0]['sha256']


def test_missing_original_sources_do_not_replace_report_or_break_reading(monkeypatch):
    def missing(*args): raise PanoramaNotFound('original archive unavailable')
    monkeypatch.setattr(SourceLibrary, 'resolve_references', missing, raising=False)
    library = ResearchLibrary(); catalog = library.catalog()
    doc = library.document(catalog['articles'][0]['id'], catalog['edition'])
    assert doc['source_reference'] is None
    assert doc['markdown'].startswith('# ')
