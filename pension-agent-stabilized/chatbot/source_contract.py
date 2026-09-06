"""Route aliases are compatibility labels, never proof of tool execution."""
from __future__ import annotations


def canonical_route(route: str | None) -> str:
    value = str(route or '')
    return 'document+product' if value == 'both' else value


def route_domains(route: str | None) -> set[str]:
    return set(canonical_route(route).split('+')) & {'document', 'product', 'law', 'calculation'}


def source_usage(result: dict, meta: dict | None = None) -> dict:
    meta = meta or {}
    lookup = bool(result.get('product_lookup_used', meta.get('product_lookup_used', False)))
    backend = result.get('product_backend') or meta.get('product_backend')
    law = result.get('law_result') or {}
    retrieval = law.get('retrieval_source')
    return {
        'product_lookup_used': lookup,
        'backend': backend if lookup else None,
        'structured_product_source': backend if lookup else None,
        'product_record_count': len(result.get('product_results') or []),
        'product_records_used': bool(result.get('product_results')),
        'document_lookup_used': bool(result.get('document_lookup_used', False)),
        'law_lookup_used': bool(law),
        'law_backend': retrieval,
        'external_api_used': bool(law.get('external_api_used', False)),
        'source_type': 'structured_product' if result.get('product_results') else None,
    }
