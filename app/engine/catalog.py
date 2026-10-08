"""Geodatabase catalog model and XML parsers (GDB_Items definitions, domains, metadata).

All XML is parsed with entity resolution and network access disabled (no XXE).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lxml import etree

FIELD_TYPE_INTEGER = ("Integer", "SmallInteger", "BigInteger", "OID")
FIELD_TYPE_FLOAT = ("Double", "Single")


@dataclass
class FieldDef:
    name: str
    type: str  # esriFieldType* (or OGR type for gpkg)
    nullable: bool = True
    required: bool = False
    domain: str | None = None
    alias: str | None = None


@dataclass
class ClassDef:
    name: str
    kind: str  # "Feature Class" | "Table"
    fields: list[FieldDef] = field(default_factory=list)
    subtype_field: str | None = None
    subtypes: dict[int, dict] = field(default_factory=dict)  # code -> {"name":..., "domains": {field_lower: domain}}
    has_z: bool | None = None
    has_m: bool | None = None
    shape_type: str | None = None
    xy_tolerance: float | None = None
    path: str | None = None
    documentation: str | None = None

    def field_map(self) -> dict[str, FieldDef]:
        return {f.name.lower(): f for f in self.fields}


@dataclass
class Domain:
    name: str
    kind: str  # "coded" | "range"
    field_type: str | None = None
    codes: dict[Any, str] = field(default_factory=dict)
    min: float | None = None
    max: float | None = None
    description: str | None = None


def _parser() -> etree.XMLParser:
    return etree.XMLParser(
        recover=True, huge_tree=True, resolve_entities=False, no_network=True, load_dtd=False, dtd_validation=False
    )


def parse_xml(s: str | bytes | None):
    """Parse an XML string safely; returns the root element or None."""
    if not s or not isinstance(s, (str, bytes)):
        return None
    data = s.encode("utf-8") if isinstance(s, str) else s
    try:
        return etree.fromstring(data, parser=_parser())
    except (etree.XMLSyntaxError, ValueError):
        return None


def strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def text(el, path: str) -> str | None:
    r = el.find(path)
    return r.text if r is not None and r.text is not None else None


def parse_class_definition(
    name: str, kind: str, xml: str | None, path: str | None = None, doc: str | None = None
) -> ClassDef:
    cd = ClassDef(name=name, kind=kind, path=path, documentation=doc)
    root = parse_xml(xml)
    if root is None:
        return cd
    root = strip_ns(root)
    for fi in root.iter("GPFieldInfoEx"):
        nm = text(fi, "Name")
        if not nm:
            continue
        cd.fields.append(
            FieldDef(
                name=nm,
                type=text(fi, "FieldType") or "",
                nullable=(text(fi, "IsNullable") or "true").lower() == "true",
                required=(text(fi, "Required") or "false").lower() == "true",
                domain=text(fi, "DomainName"),
                alias=text(fi, "AliasName"),
            )
        )
    cd.subtype_field = text(root, "SubtypeFieldName")
    for st in root.iter("Subtype"):
        code = text(st, "SubtypeCode")
        if code is None:
            continue
        doms = {}
        for sfi in st.iter("SubtypeFieldInfo"):
            fn, dn = text(sfi, "FieldName"), text(sfi, "DomainName")
            if fn and dn:
                doms[fn.lower()] = dn
        try:
            cd.subtypes[int(code)] = {"name": text(st, "SubtypeName"), "domains": doms}
        except ValueError:
            pass
    hz, hm = text(root, "HasZ"), text(root, "HasM")
    cd.has_z = None if hz is None else hz.lower() == "true"
    cd.has_m = None if hm is None else hm.lower() == "true"
    cd.shape_type = text(root, "ShapeType")
    tol = text(root, "XYTolerance")
    try:
        cd.xy_tolerance = float(tol) if tol else None
    except ValueError:
        pass
    return cd


def coerce_code(code: str | None, ftype: str | None) -> Any:
    if code is None:
        return None
    if ftype and any(t in ftype for t in FIELD_TYPE_INTEGER):
        try:
            return int(float(code))
        except ValueError:
            return code
    if ftype and any(t in ftype for t in FIELD_TYPE_FLOAT):
        try:
            return float(code)
        except ValueError:
            return code
    return code


def parse_domain(xml: str | None) -> Domain | None:
    root = parse_xml(xml)
    if root is None:
        return None
    root = strip_ns(root)
    name = text(root, "DomainName")
    if not name:
        return None
    ftype = text(root, "FieldType")
    if root.tag.startswith("GPCodedValueDomain") or root.find(".//CodedValues") is not None:
        d = Domain(name=name, kind="coded", field_type=ftype, description=text(root, "Description"))
        for cv in root.iter("CodedValue"):
            d.codes[coerce_code(text(cv, "Code"), ftype)] = text(cv, "Name") or ""
        return d
    d = Domain(name=name, kind="range", field_type=ftype, description=text(root, "Description"))
    try:
        d.min = float(text(root, "MinValue"))  # type: ignore[arg-type]
        d.max = float(text(root, "MaxValue"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        pass
    return d
