FROM python:3.12-slim

WORKDIR /srv/app

COPY requirements.txt .
# WeasyPrint renders through pango/cairo, which are not in python:*-slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
        libjpeg62-turbo libopenjp2-7 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
