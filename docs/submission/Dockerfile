# Одноразовый образ для сборки сопроводительной документации (docs/submission/README.md).
# pandoc — Markdown → .docx; LibreOffice (headless, UNO) — обновить оглавление и сохранить .pdf;
# Graphviz — схемы; шрифты PT Sans/PT Serif (ParaType, кириллица); python-docx — постобработка таблиц;
# poppler-utils — рендер страниц PDF в PNG для проверки глазами. В Windows ничего не ставится.
FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
        pandoc libreoffice-writer-nogui python3-uno graphviz fonts-paratype fonts-liberation2 \
        python3-docx poppler-utils fontconfig \
    && fc-cache -f && rm -rf /var/lib/apt/lists/*
WORKDIR /work
