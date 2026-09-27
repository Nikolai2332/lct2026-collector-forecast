"""Русские сообщения для ошибок валидации (422). Структура ответа — как у FastAPI по умолчанию."""

from typing import Any

MESSAGES = {
    "missing": "Обязательное поле",
    "int_parsing": "Нужно целое число",
    "int_type": "Нужно целое число",
    "float_parsing": "Нужно число",
    "float_type": "Нужно число",
    "bool_parsing": "Нужно true или false",
    "string_type": "Нужна строка",
    "string_too_short": "Слишком короткое значение",
    "string_too_long": "Слишком длинное значение",
    "datetime_parsing": "Нужна дата и время в формате ISO 8601, например 2026-08-01T12:00:00",
    "datetime_from_date_parsing": "Нужна дата и время в формате ISO 8601, например 2026-08-01T12:00:00",
    "date_parsing": "Нужна дата в формате ГГГГ-ММ-ДД",
    "date_from_datetime_parsing": "Нужна дата в формате ГГГГ-ММ-ДД",
    "literal_error": "Недопустимое значение",
    "enum": "Недопустимое значение",
    "greater_than": "Значение слишком маленькое",
    "greater_than_equal": "Значение слишком маленькое",
    "less_than": "Значение слишком большое",
    "less_than_equal": "Значение слишком большое",
    "too_long": "Слишком много элементов",
    "json_invalid": "Некорректный JSON",
    "model_attributes_type": "Нужен объект JSON",
    "dict_type": "Нужен объект JSON",
    "list_type": "Нужен список",
}


def translate_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for err in errors:
        item = {k: v for k, v in err.items() if k in ("type", "loc", "msg", "input")}
        if err.get("type") == "value_error":
            # Сообщения из наших валидаторов уже на русском: «Value error, Укажите …»
            item["msg"] = str(err.get("msg", "")).removeprefix("Value error, ")
        elif err.get("type") in MESSAGES:
            item["msg"] = MESSAGES[err["type"]]
            if err["type"] == "literal_error" and err.get("ctx", {}).get("expected"):
                item["msg"] += f": ожидается {err['ctx']['expected']}"
        # input может содержать несериализуемые объекты (файлы) — оставляем только простые типы
        if not isinstance(item.get("input"), (str, int, float, bool, type(None), dict, list)):
            item.pop("input", None)
        result.append(item)
    return result
