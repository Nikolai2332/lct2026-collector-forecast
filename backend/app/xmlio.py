"""Обмен XML (ТЗ, раздел 7: «Программный обмен (API): JSON, XML»).

Разбор — только через defusedxml с запретом DTD, сущностей и внешних ссылок: XXE («прочитать file:///etc/passwd»),
«миллиард смешков» (экспоненциальное раскрытие сущностей) и обращения к внешним DTD отклоняются до разбора
содержимого. Размер документа ограничивает вызывающий код (как для JSON/CSV).

Форматы (примеры — docs/API.md):
  поток событий   <events><event><channel_id>…</channel_id><ts>…</ts><is_alarm>…</is_alarm><value>…</value></event>…</events>
  импорт таблиц   <rows><row><поле>значение</поле>…</row>…</rows> — имена полей те же, что заголовки CSV;
                  значения можно давать и атрибутами: <row channel_id="…" value="…"/>
  журнал          <journal at="…" rows="N"><prediction id="…">…</prediction>…</journal>
"""

import xml.etree.ElementTree as ET  # только для записи; чтение — defusedxml
from collections.abc import Iterable
from datetime import date, datetime

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

XML_MEDIA_TYPES = ("application/xml", "text/xml")


def is_xml_content_type(content_type: str | None) -> bool:
    return (content_type or "").split(";")[0].strip().lower() in XML_MEDIA_TYPES


def parse(content: bytes) -> ET.Element:
    """Безопасный разбор. Любая DTD, сущность или внешняя ссылка — ValueError, как и битый XML."""
    try:
        return fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException:
        raise ValueError("XML с DTD или сущностями не принимается (защита от XXE и «миллиарда смешков»)")
    except ParseError as e:
        raise ValueError(f"XML не разбирается: {e}")


def _local(tag: str) -> str:
    """Имя без пространства имён: {urn:x}row → row."""
    return tag.rsplit("}", 1)[-1]


def element_fields(el: ET.Element) -> dict[str, str]:
    """Поля записи: атрибуты и дочерние элементы (текст). Вложенные глубже элементы не разбираются."""
    fields = {_local(k): v for k, v in el.attrib.items()}
    for child in el:
        fields[_local(child.tag)] = (child.text or "").strip()
    return fields


def records(root: ET.Element) -> list[dict[str, str]]:
    """Записи документа: дочерние элементы корня (row, event, … — имя не важно)."""
    return [element_fields(el) for el in root]


def events_payload(content: bytes) -> dict:
    """XML потока событий → тот же словарь, что JSON: {"events": [...]}; проверяет его затем схема EventsIn."""
    root = parse(content)
    if _local(root.tag) != "events":
        raise ValueError("Корневой элемент XML потока событий — <events>")
    events = []
    for rec in records(root):
        ev = {k: v for k, v in rec.items() if k in ("event_id", "channel_id", "ts", "is_alarm", "value")}
        if ev.get("event_id") == "":
            ev.pop("event_id")
        events.append(ev)
    return {"events": events}


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def build(root_tag: str, attrs: dict, items: Iterable[tuple[str, dict, dict]]) -> bytes:
    """Документ: корень с атрибутами, записи (тег, атрибуты, поля). Пустые поля не пишутся."""
    root = ET.Element(root_tag, {k: _text(v) for k, v in attrs.items()})
    for tag, item_attrs, fields in items:
        el = ET.SubElement(root, tag, {k: _text(v) for k, v in item_attrs.items() if v is not None})
        for name, value in fields.items():
            if value is None or value == "":
                continue
            ET.SubElement(el, name).text = _text(value)
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
