FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 LS_DATA_DIR=/data LS_HOST=0.0.0.0 LS_PORT=8000
WORKDIR /app
# Зависимости ставятся строго по списку с контрольными суммами: подменённый или внезапно обновлённый пакет не пройдёт
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY leadscout ./leadscout
RUN pip install --no-cache-dir --no-deps .

RUN useradd --create-home --uid 1000 scout && mkdir /data && chown scout /data
USER scout
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "leadscout"]
