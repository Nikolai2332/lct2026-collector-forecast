"""Перечень библиотек и компонентов для сопроводительной документации — из фактически установленных пакетов.

Два режима:
    python gen_deps.py inventory <requirements.txt> <out.json>
        запускается ВНУТРИ окружения (контейнер api, ml/.venv): версии и лицензии из importlib.metadata
        всех установленных пакетов; «прямые» — те, что названы в requirements.
    python gen_deps.py markdown <deps-dir> <package-lock.json> <out.md>
        собирает таблицы Markdown: прямые зависимости (основной текст) и все транзитивные (приложение);
        фронтенд — из package-lock.json (версия и лицензия каждого пакета записаны npm в lock-файле).

Руками ничего не набирается, кроме колонки «назначение» у прямых зависимостей (словарь PURPOSE ниже).
"""

import json
import re
import sys
from pathlib import Path

PURPOSE = {
    # бэкенд
    "fastapi": "веб-фреймворк REST API, схема OpenAPI",
    "uvicorn": "ASGI-сервер для FastAPI",
    "sqlalchemy": "ORM и построитель SQL-запросов",
    "alembic": "миграции схемы PostgreSQL",
    "psycopg": "драйвер PostgreSQL",
    "psycopg-binary": "скомпилированная часть драйвера PostgreSQL",
    "pydantic": "схемы данных, проверка входа и выхода API",
    "pydantic-settings": "настройки из переменных окружения",
    "pyjwt": "JWT-токены входа",
    "openpyxl": "чтение и запись XLSX (импорт, выгрузки)",
    "python-multipart": "загрузка файлов (импорт)",
    "apscheduler": "планировщик: такт симуляции, разметка исходов, прогрев кэша",
    "pyyaml": "правила рекомендаций rules.yaml",
    "defusedxml": "безопасный разбор XML (защита от XXE)",
    "ldap3": "вход через LDAP/Active Directory",
    "pytest": "автотесты бэкенда",
    "httpx": "HTTP-клиент тестов",
    # ML
    "duckdb": "ETL журналов (313 млн строк) и расчёт признаков",
    "lightgbm": "модель градиентного бустинга",
    "matplotlib": "графики отчёта ML",
    "numpy": "численные массивы",
    "pandas": "таблицы данных",
    "polars": "быстрые таблицы данных",
    "pyarrow": "формат Parquet",
    "scikit-learn": "метрики, калибровка вероятностей",
    "scipy": "статистика",
    "shap": "вклад признаков в прогноз (причины)",
    # фронтенд
    "@ant-design/icons": "иконки интерфейса",
    "@tanstack/react-query": "загрузка и кэш данных API",
    "antd": "компоненты интерфейса Ant Design",
    "dayjs": "даты и время",
    "echarts": "графики и схема коллектора",
    "react": "библиотека интерфейса",
    "react-dom": "вывод React в браузер",
    "react-router-dom": "маршрутизация экранов",
    "@eslint/js": "правила ESLint",
    "@types/node": "типы Node.js для TypeScript",
    "@types/react": "типы React",
    "@types/react-dom": "типы React DOM",
    "@vitejs/plugin-react": "React для сборщика Vite",
    "eslint": "проверка кода (lint)",
    "eslint-plugin-react-hooks": "правила хуков React",
    "eslint-plugin-react-refresh": "правила горячей перезагрузки",
    "globals": "глобальные имена для ESLint",
    "msw": "моки API для разработки без бэкенда",
    "openapi-typescript": "типы TypeScript из openapi.json",
    "playwright": "проход по экранам и скриншоты",
    "typescript": "язык TypeScript, проверка типов",
    "typescript-eslint": "ESLint для TypeScript",
    "vite": "сборщик фронтенда",
}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def license_of(meta) -> str:
    lic = (meta.get("License-Expression") or "").strip()
    if not lic:
        raw = (meta.get("License") or "").strip()
        if raw and len(raw) < 60 and "\n" not in raw:
            lic = raw
    if not lic:
        cls = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
        cls = [c for c in cls if c not in ("OSI Approved",)]
        lic = ", ".join(dict.fromkeys(cls))
    return lic or "см. пакет"


def inventory(req_path: str, out: str) -> None:
    from importlib import metadata

    direct = set()
    for line in Path(req_path).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-r"):
            continue
        direct.add(norm(re.split(r"[\[<>=!~ ]", line, maxsplit=1)[0]))
    rows = []
    for dist in metadata.distributions():
        m = dist.metadata
        name = m.get("Name")
        if not name or norm(name) in ("pip", "setuptools", "wheel"):
            continue
        rows.append({"name": name, "version": dist.version, "license": license_of(m),
                     "summary": (m.get("Summary") or "").strip(), "direct": norm(name) in direct})
    rows.sort(key=lambda r: norm(r["name"]))
    import platform

    Path(out).write_text(json.dumps({"python": platform.python_version(), "packages": rows}, ensure_ascii=False,
                                    indent=1), encoding="utf-8")
    print(f"{out}: {len(rows)} пакетов, прямых {sum(r['direct'] for r in rows)}, Python {platform.python_version()}")


def esc(s: str) -> str:
    return (s or "").replace("|", "/").replace("\n", " ")


def npm_packages(lock_path: str):
    lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))
    root = lock["packages"][""]
    deps, dev = root.get("dependencies", {}), root.get("devDependencies", {})
    rows = {}
    for path, p in lock["packages"].items():
        if not path:
            continue
        name = path.split("node_modules/")[-1]
        key = (name, p.get("version"))
        if key in rows:
            continue
        rows[key] = {"name": name, "version": p.get("version", ""), "license": p.get("license", "см. пакет"),
                     "dev": bool(p.get("dev")),
                     # прямая зависимость — только пакет верхнего уровня (вложенные копии других версий — транзитивные)
                     "direct": path == f"node_modules/{name}" and (name in deps or name in dev),
                     "kind": "сборка и проверка" if name in dev else ("работа" if name in deps else "")}
    return sorted(rows.values(), key=lambda r: r["name"]), deps, dev


def markdown(deps_dir: str, lock_path: str, out: str) -> None:
    d = Path(deps_dir)
    parts, appendix, lic_all = [], [], {}
    for key, title in (("backend", "Бэкенд (контейнер api, Python {py})"), ("ml", "ML-конвейер (окружение ml/, Python {py})")):
        data = json.loads((d / f"{key}.json").read_text(encoding="utf-8"))
        pk = data["packages"]
        for r in pk:
            lic_all[r["license"]] = lic_all.get(r["license"], 0) + 1
        direct = [r for r in pk if r["direct"]]
        parts.append(f"**{title.format(py=data['python'])}** — прямых зависимостей {len(direct)}, всего установлено "
                     f"пакетов {len(pk)}.\n")
        parts.append("| Компонент | Версия | Назначение | Лицензия |\n|---|---|---|---|")
        parts += [f"| {r['name']} | {r['version']} | {PURPOSE.get(norm(r['name']), esc(r['summary']))} | {esc(r['license'])} |"
                  for r in direct]
        parts.append("")
        appendix.append(f"**{title.format(py=data['python'])}, все установленные пакеты ({len(pk)})**\n")
        appendix.append("| Пакет | Версия | Лицензия |\n|---|---|---|")
        appendix += [f"| {r['name']} | {r['version']} | {esc(r['license'])} |" for r in pk]
        appendix.append("")
    npm, deps, dev = npm_packages(lock_path)
    for r in npm:
        lic_all[r["license"]] = lic_all.get(r["license"], 0) + 1
    direct = [r for r in npm if r["direct"]]
    parts.append(f"**Фронтенд (Node.js, package-lock.json)** — прямых зависимостей {len(direct)} "
                 f"({len(deps)} для работы, {len(dev)} для сборки и проверки), всего пакетов в lock-файле {len(npm)}.\n")
    parts.append("| Компонент | Версия | Назначение | Для чего | Лицензия |\n|---|---|---|---|---|")
    parts += [f"| {r['name']} | {r['version']} | {PURPOSE.get(r['name'], '')} | {r['kind']} | {esc(r['license'])} |"
              for r in direct]
    parts.append("")
    appendix.append(f"**Фронтенд, все пакеты package-lock.json ({len(npm)})**\n")
    appendix.append("| Пакет | Версия | Лицензия |\n|---|---|---|")
    appendix += [f"| {r['name']} | {r['version']} | {esc(r['license'])} |" for r in npm]
    lic_rows = sorted(lic_all.items(), key=lambda kv: -kv[1])
    lic_md = ["| Лицензия | Пакетов |", "|---|---|"] + [f"| {esc(k)} | {v} |" for k, v in lic_rows]
    Path(out).write_text("\n".join(parts) + "\n<!--LICENSES-->\n" + "\n".join(lic_md) + "\n<!--APPENDIX-->\n"
                         + "\n".join(appendix) + "\n", encoding="utf-8")
    print(f"{out}: лицензий {len(lic_rows)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1] == "inventory":
        inventory(sys.argv[2], sys.argv[3])
    else:
        markdown(sys.argv[2], sys.argv[3], sys.argv[4])
