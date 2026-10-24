FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# System packages:
#  - pango/cairo/gdk-pixbuf + fonts  -> WeasyPrint (Persian/RTL cover sheets)
#  - tesseract-ocr + fas/eng data    -> OCR fallback
#  - libgl/libglib for PyMuPDF/pillow image ops
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
        libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
        libffi-dev shared-mime-info \
        fonts-noto-core fonts-noto-extra fonts-dejavu-core \
        tesseract-ocr tesseract-ocr-fas tesseract-ocr-eng \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Optional Persian UI font (Vazirmatn) — build does not fail without network.
RUN mkdir -p /usr/share/fonts/truetype/vazirmatn && \
    (curl -fsSL -o /tmp/vaz.zip \
      https://github.com/rastikerdar/vazirmatn/releases/download/v33.003/vazirmatn-v33.003.zip \
     && cd /tmp && python -c "import zipfile;zipfile.ZipFile('/tmp/vaz.zip').extractall('/tmp/vaz')" \
     && find /tmp/vaz -name "*.ttf" -exec cp {} /usr/share/fonts/truetype/vazirmatn/ \; \
     && fc-cache -f >/dev/null 2>&1) || echo "Vazirmatn font download skipped (offline build)"

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY . /app

RUN useradd -m -u 10001 edms && mkdir -p /data/uploads && chown -R edms:edms /app /data
USER edms

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
