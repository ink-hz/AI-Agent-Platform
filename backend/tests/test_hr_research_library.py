import shutil
from pathlib import Path

import pytest

from app.hr.research_library import ResearchLibrary
from app.hr.panorama_repository import PanoramaNotFound, PanoramaUnavailable


def test_real_research_edition_is_complete_and_links_are_resolvable():
    library = ResearchLibrary()
    catalog = library.catalog()
    assert len(catalog['articles']) == 38
    assert len(catalog['companies']) == 7
    assert catalog['covered_job_identities'] == 3355
    for entry in catalog['articles']:
        doc = library.document(entry['id'], catalog['edition'])
        assert doc['markdown'].startswith('# ')
        assert doc['sha256'] == entry['sha256']
        for link in doc['links'].values():
            library.document(link, catalog['edition'])
    with pytest.raises(PanoramaNotFound):
        library.document(catalog['articles'][0]['id'], 'missing-edition')
    with pytest.raises(PanoramaNotFound):
        library.document('../manifest.json', catalog['edition'])


def test_changed_body_cannot_masquerade_as_published_research(tmp_path):
    source = Path(__file__).parents[1] / 'app/hr/research_content'
    shutil.copytree(source, tmp_path / 'content')
    library = ResearchLibrary(tmp_path / 'content')
    catalog = library.catalog()
    entry = catalog['articles'][0]
    path = tmp_path / 'content' / entry['path']
    path.write_text('# tampered')
    with pytest.raises(PanoramaUnavailable):
        library.document(entry['id'], catalog['edition'])
