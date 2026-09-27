# Сопроводительная документация (.docx и .pdf)

Исходник — `doc.md` (Markdown, pandoc) + схемы `diagrams/*.dot` (Graphviz) + скриншоты демо `../screenshots/1366x768/`
и графики ML `../ml_v3_*.png`. Таблица соответствия ТЗ вставляется из `../TZ_COVERAGE.md`, перечень библиотек — из
`deps/` (генерируется `gen_deps.py` из фактически установленных пакетов и `frontend/package-lock.json`).

## Сборка одной командой (Windows, PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File docs/submission/build.ps1
```

Результат — `dist/submission/Документация_ОтказДатчика_ЛЦТ2026.docx` и `.pdf` (каталог `dist/` вне git).

Второй вариант — со скриншотами реальных данных (срез 29.06.2026) вместо демо: ключ `-Real`. Скриншоты берутся
из `backups/prompt10_real/clean_slice/` (вне git) и копируются в `build/real/`; результат —
`…_реальные_данные.docx` и `.pdf`. Текст документа тот же, меняются только рисунки 3–8 и их подписи.

```powershell
powershell -ExecutionPolicy Bypass -File docs/submission/build.ps1 -Real
```

Всё выполняется в одноразовом контейнере `collector-docs:1` (образ собирается из `Dockerfile` рядом, ≈ 1 мин при
первом запуске): Debian bookworm + pandoc 2.17, LibreOffice 7.4 (headless, UNO), Graphviz 2.43, шрифты PT Sans /
PT Serif / PT Mono (ParaType), python-docx, poppler-utils. В Windows ничего не устанавливается.

Если запущен основной стек (`docker compose up`) и есть окружение `ml/.venv`, `build.ps1` сначала обновит перечень
библиотек (`deps/backend.json` — из контейнера api, `deps/ml.json` — из `ml/.venv`); иначе берутся сохранённые файлы.

## Шаги внутри контейнера (`build.sh`)

1. `dot` — схемы `diagrams/*.dot` → `img/diag_*.png`.
2. `assemble.py` — `build/full.md`: вставки таблиц ТЗ и библиотек, ширина колонок по содержимому.
3. `make_reference.py` — шаблон стилей `reference.docx` (шрифты с кириллицей, заголовок 1 с новой страницы,
   колонтитул с названием и номером страницы, A4).
4. `pandoc` — `.docx` с автоматическим оглавлением, нумерацией разделов; `number.lua` — «Рисунок N», «Таблица N».
5. `postprocess.py` — рамки таблиц, повтор шапки на каждой странице, строки не разрываются между страницами.
6. `uno_export.py` — LibreOffice обновляет оглавление (номера страниц) и сохраняет `.docx` и `.pdf` со встроенными
   шрифтами.
7. `check_docx.py` — проверка: заголовки, таблицы, рисунки, пустые заголовки, поиск запрещённого (личные пути,
   секреты, рабочие файлы, «сертифицировано/гарантирует»).

Без PowerShell (Linux, macOS, Git Bash) — те же шаги вручную:

```bash
docker build -t collector-docs:1 docs/submission
docker run --rm -v "$PWD/docs:/docs" -v "$PWD/dist/submission:/out" collector-docs:1 sh /docs/submission/build.sh
```

## Проверка фактов

`FACTCHECK.md` — каждое число документа и его источник; расхождения между документами проекта — в конце файла.

## Что заполнить перед сдачей

В `doc.md`: `[Название команды]`, `[Состав команды]` (титул), `[ссылка на архив среза]` (разделы 1 и 11.3).
