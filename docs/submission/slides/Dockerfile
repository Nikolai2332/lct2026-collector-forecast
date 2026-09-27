# Одноразовый образ для сборки и проверки презентации: python-pptx (сборка из шаблона), LibreOffice Impress
# (pptx → pdf), poppler-utils (pdf → png), шрифт Montserrat из шаблона организаторов. Debian trixie: в bookworm
# (основа collector-docs:1) пакета fonts-montserrat нет. В Windows ничего не ставится.
FROM debian:trixie-slim
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-impress-nogui fonts-montserrat fonts-paratype fonts-liberation2 poppler-utils fontconfig \
        python3-venv python3-lxml python3-pil \
    && fc-cache -f && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /opt/venv && /opt/venv/bin/pip install --no-cache-dir python-pptx==1.0.2
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /work
