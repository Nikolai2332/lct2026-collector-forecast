#!/bin/sh
# Рендер презентации для проверки глазами: pptx → pdf → png (внутри collector-slides:1).
# Использование: render.sh <файл.pptx> <папка для png>
set -e
f="$1"; out="${2:-png}"
dir=$(dirname "$f")
soffice --headless --convert-to pdf --outdir "$dir" "$f" >/dev/null 2>&1
pdf="${f%.pptx}.pdf"
mkdir -p "$out" && rm -f "$out"/*.png
pdftoppm -png -r 80 "$pdf" "$out/s"
pdfinfo "$pdf" | grep -E "Pages|Page size"
