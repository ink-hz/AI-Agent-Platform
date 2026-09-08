"""Project published topic metadata and accepted analysis without re-analysis."""
from collections.abc import Mapping

from .company_intelligence import (
    RESPONSE_FIELDS,
    _bundle_id,
    _iso,
    _mapping,
    _sequence,
    _text,
)
from .panorama_repository import PanoramaNotFound, PanoramaUnavailable

_FIELDS = ('topic_id', 'title', 'question', 'scope', 'analysis_state', 'unit_ids', 'discussed_companies', 'limitations')


def _items(record):
    catalog = _mapping(record.get('source_catalog'), 'catalog')
    return [_mapping(item, 'topic') for item in _sequence(catalog.get('topics', []), 'topics')]


def _units(record):
    return {_text(unit.get('unit_id'), 'unit ID'): unit for item in _sequence(record.get('analysis'), 'analysis') if (unit := _mapping(item, 'unit')).get('kind') in {'topic', 'track'}}


def _summary(topic, units):
    if any(field not in topic for field in _FIELDS):
        raise PanoramaUnavailable('published topic metadata incomplete')
    ids = _sequence(topic.get('unit_ids'), 'topic unit IDs')
    if any(unit_id not in units for unit_id in ids):
        raise PanoramaUnavailable('published topic analysis missing')
    # Each summary belongs to a declared unit; never select a different scope.
    summaries = [_text(_mapping(units[key].get('response'), 'response').get('summary'), 'summary') for key in ids]
    return {**{field: topic[field] for field in _FIELDS}, 'summary': '\n\n'.join(summaries) or None}


def project_topics(record: Mapping[str, object]) -> dict[str, object]:
    items, units = _items(record), _units(record)
    catalog = _mapping(record.get('source_catalog'), 'catalog')
    return {'bundle_id': str(_bundle_id(record)), 'generated_at': _iso(record.get('generated_at')),
            'state': 'available' if 'topics' in catalog else 'metadata_missing',
            'items': [_summary(topic, units) for topic in items]}


def project_topic(record: Mapping[str, object], topic_id: str) -> dict[str, object]:
    topic = next((item for item in _items(record) if item.get('topic_id') == topic_id), None)
    if topic is None:
        raise PanoramaNotFound('topic not found in panorama bundle')
    units = _units(record)
    summary = _summary(topic, units)
    selected = []
    for key in topic['unit_ids']:
        unit = units[key]
        response = _mapping(unit.get('response'), 'response')
        if any(field not in response for field in RESPONSE_FIELDS):
            raise PanoramaUnavailable('published topic response incomplete')
        selected.append({'unit_id': key, 'kind': unit['kind'], 'scope_key': unit['scope_key'],
                         'response': {field: response[field] for field in RESPONSE_FIELDS}})
    keys = {item['company_key'] for item in topic['discussed_companies']}
    catalog = _mapping(record.get('source_catalog'), 'catalog')
    companies = [{'company_key': item['company_key'], 'canonical_name': item['canonical_name']}
                 for item in catalog['companies'] if item['company_key'] in keys]
    if len(companies) != len(keys):
        raise PanoramaUnavailable('published topic company relationship missing')
    return {'bundle_id': str(_bundle_id(record)), 'generated_at': _iso(record.get('generated_at')),
            'topic': summary, 'units': selected, 'companies': companies}
