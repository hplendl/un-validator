"""XML parsing (incl. XXE safety), class/domain definitions and metadata scoring."""

from app.engine.catalog import parse_class_definition, parse_domain, parse_xml
from app.engine.checks.metadata import evaluate_document, score_document

from .synth import FULL_ARCGIS_DOC, PLACEHOLDER_DOC

CLASS_XML = """<DEFeatureClassInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="typens:DEFeatureClassInfo"
 xmlns:typens="http://www.esri.com/schemas/ArcGIS/10.1">
 <GPFieldInfoExs>
  <GPFieldInfoEx><Name>OBJECTID</Name><FieldType>esriFieldTypeOID</FieldType><IsNullable>false</IsNullable></GPFieldInfoEx>
  <GPFieldInfoEx><Name>ASSETGROUP</Name><FieldType>esriFieldTypeInteger</FieldType><IsNullable>false</IsNullable><Required>true</Required></GPFieldInfoEx>
  <GPFieldInfoEx><Name>ASSETTYPE</Name><FieldType>esriFieldTypeSmallInteger</FieldType><DomainName>AT</DomainName></GPFieldInfoEx>
 </GPFieldInfoExs>
 <SubtypeFieldName>ASSETGROUP</SubtypeFieldName>
 <Subtypes><Subtype><SubtypeName>Valve</SubtypeName><SubtypeCode>3</SubtypeCode>
  <FieldInfos><SubtypeFieldInfo><FieldName>ASSETTYPE</FieldName><DomainName>AT_Valve</DomainName></SubtypeFieldInfo></FieldInfos>
 </Subtype></Subtypes>
 <HasZ>true</HasZ><HasM>false</HasM><ShapeType>esriGeometryPoint</ShapeType><XYTolerance>0.001</XYTolerance>
</DEFeatureClassInfo>"""

CODED_XML = """<GPCodedValueDomain2 xmlns:typens="http://www.esri.com/schemas/ArcGIS/10.1">
<DomainName>AT</DomainName><FieldType>esriFieldTypeSmallInteger</FieldType>
<CodedValues><CodedValue><Name>Gate</Name><Code>1</Code></CodedValue><CodedValue><Name>Ball</Name><Code>2</Code></CodedValue></CodedValues>
</GPCodedValueDomain2>"""

RANGE_XML = """<GPRangeDomain2><DomainName>Pressure</DomainName><FieldType>esriFieldTypeDouble</FieldType>
<MinValue>0</MinValue><MaxValue>150.5</MaxValue></GPRangeDomain2>"""


def test_parse_class_definition():
    cd = parse_class_definition("Device", "Feature Class", CLASS_XML)
    fm = cd.field_map()
    assert not fm["assetgroup"].nullable and fm["assetgroup"].required
    assert fm["assettype"].domain == "AT"
    assert cd.subtype_field == "ASSETGROUP"
    assert cd.subtypes[3] == {"name": "Valve", "domains": {"assettype": "AT_Valve"}}
    assert cd.has_z is True and cd.has_m is False and cd.xy_tolerance == 0.001


def test_parse_domains():
    d = parse_domain(CODED_XML)
    assert d.kind == "coded" and d.codes == {1: "Gate", 2: "Ball"}
    r = parse_domain(RANGE_XML)
    assert r.kind == "range" and (r.min, r.max) == (0.0, 150.5)
    assert parse_domain("<not xml") is None and parse_domain(None) is None


def test_broken_or_empty_xml_gives_empty_definition():
    cd = parse_class_definition("X", "Table", "<<<garbage")
    assert cd.fields == [] and cd.has_z is None


def test_xml_external_entities_are_not_resolved(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    xxe = f'<?xml version="1.0"?><!DOCTYPE m [<!ENTITY x SYSTEM "{secret.as_uri()}">]><metadata><idAbs>&x;</idAbs></metadata>'
    root = parse_xml(xxe)
    text = "".join(root.itertext()) if root is not None else ""
    assert "TOP-SECRET" not in text
    evaluate_document(xxe)  # must not raise


def test_evaluate_full_arcgis_document():
    ev = evaluate_document(FULL_ARCGIS_DOC)
    assert ev["_standard"] == "ArcGIS"
    assert all(
        ev[k] == "present"
        for k in ("summary", "description", "tags", "credits", "use_limits", "contact", "extent", "lineage")
    )
    assert score_document(ev) == 100.0


def test_evaluate_placeholder_and_missing():
    ev = evaluate_document(PLACEHOLDER_DOC)
    assert ev["summary"] == "placeholder" and ev["description"] == "placeholder" and ev["tags"] == "missing"
    assert score_document(ev) == 0.0
    assert evaluate_document(None)["summary"] == "missing"


def test_evaluate_fgdc_and_iso():
    fgdc = "<metadata><idinfo><descript><abstract>Water mains</abstract><purpose>Ops</purpose></descript></idinfo></metadata>"
    ev = evaluate_document(fgdc)
    assert ev["_standard"] == "FGDC CSDGM" and ev["description"] == "present" and ev["summary"] == "present"
    iso = (
        '<gmd:MD_Metadata xmlns:gmd="http://www.isotc211.org/2005/gmd" xmlns:gco="http://www.isotc211.org/2005/gco">'
        "<gmd:abstract><gco:CharacterString>Mains</gco:CharacterString></gmd:abstract></gmd:MD_Metadata>"
    )
    ev = evaluate_document(iso)
    assert ev["_standard"] == "ISO 19139" and ev["description"] == "present"


def test_auto_lineage_gets_partial_credit():
    doc = "<metadata><Esri><DataProperties><lineage><Process>Append</Process></lineage></DataProperties></Esri></metadata>"
    ev = evaluate_document(doc)
    assert ev["lineage"] == "auto"
    assert 0 < score_document(ev) < 100
