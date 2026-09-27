#!/bin/sh
# Сборка документации в контейнере collector-docs (см. README.md). Вход: /docs (каталог docs/ проекта), выход: /out.
set -eu
cd /docs/submission
NAME="${OUT_NAME:-Документация_ОтказДатчика_ЛЦТ2026}"
[ "${DOC_SCREENS:-}" = real ] && NAME="${NAME}_реальные_данные"
mkdir -p build /out
for f in diagrams/*.dot; do dot -Tpng -Gdpi=200 "$f" -o "img/diag_$(basename "$f" .dot).png"; done
python3 assemble.py
pandoc -o build/default_ref.docx --print-default-data-file reference.docx
python3 make_reference.py build/default_ref.docx build/reference.docx
pandoc build/full.md -o build/doc.docx --reference-doc build/reference.docx --toc --toc-depth=2 \
  --number-sections --lua-filter number.lua --resource-path=.:.. -M toc-title="Содержание"
python3 postprocess.py build/doc.docx
python3 uno_export.py build/doc.docx build/doc.pdf
cp build/doc.docx "/out/$NAME.docx"
cp build/doc.pdf "/out/$NAME.pdf"
pdftotext -layout build/doc.pdf build/pdf.txt
pdfinfo "/out/$NAME.pdf" | grep -E "Pages|Page size"
ls -la /out

python3 check_docx.py "/out/$NAME.docx" build/pdf.txt
