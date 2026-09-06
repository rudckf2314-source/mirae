"""Exact numeric tokens with units; no substring or floor-division matching."""
from __future__ import annotations

import re
from decimal import Decimal

_FACT = re.compile(r'(?<![A-Za-z0-9])(?P<n>[+-]?\d[\d,]*(?:\.\d+)?)\s*(?P<u>억\s*원|만\s*원|원|%|퍼센트|영업일|개월|등급|년|월|일|세|시간|분|초|개|건)?')
_COMPOUND = re.compile(r'(?<!\d)(?P<date>20\d{2}[-./]\d{1,2}[-./]\d{1,2})|(?P<time>\b\d{1,2}:\d{2}\b)')


def numeric_facts(text: str) -> list[tuple[str, str, str]]:
    text = str(text or '')
    text = re.sub(r'\S+\.(?:pdf|docx|json)\b', '', text, flags=re.I)
    text = re.sub(r'(?m)^\s*\d+[.)]\s+', '', text)  # list item index
    text = re.sub(r'100\s*분의\s*(\d+(?:\.\d+)?)', r'\1%', text)
    out=[]
    for m in _COMPOUND.finditer(text):
        unit='date' if m.group('date') else 'time'
        numbers=[str(int(x)) for x in re.findall(r'\d+',m.group())]
        out.append((m.group(), ':'.join(numbers), unit))
    text=_COMPOUND.sub('',text)
    text=re.sub(r'\b[A-Za-z][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*\b','',text)
    for m in _FACT.finditer(text):
        value=Decimal(m.group('n').replace(',',''))
        unit=re.sub(r'\s+','',m.group('u') or '')
        if unit in {'만원','억원'}:
            value*=Decimal('10000' if unit=='만원' else '100000000');unit='원'
        if unit=='퍼센트':unit='%'
        out.append((m.group().strip(),str(value.normalize()),unit))
    return list(dict.fromkeys(out))


def contains_fact(text: str, token: str, *, allow_unspecified_unit: bool = False) -> bool:
    target=numeric_facts(token)
    found={(v,u) for _,v,u in numeric_facts(text)}
    return bool(target) and all((v,u) in found or (allow_unspecified_unit and not u and any(v==fv for fv,fu in found)) for _,v,u in target)
