ARG PYTHON_BASE=python:3.11-slim
FROM ${PYTHON_BASE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MOLECULE_RANKER_HOSTED=true \
    MOLECULE_RANKER_ROOT=/data/projects \
    MOLECULE_RANKER_ARTIFACT_ROOT=/data/artifacts \
    MOLECULE_RANKER_STORAGE_ROOT=/data/storage \
    MOLECULE_RANKER_HOST=0.0.0.0 \
    MOLECULE_RANKER_PORT=8765

WORKDIR /app

USER root

RUN getent group molecule-ranker >/dev/null || addgroup --system molecule-ranker \
    && id -u molecule-ranker >/dev/null 2>&1 || adduser --system --ingroup molecule-ranker --home /app molecule-ranker \
    && mkdir -p /data/projects /data/artifacts /data/storage \
    && chown -R molecule-ranker:molecule-ranker /app /data

COPY pyproject.toml README.md ./
COPY molecule_ranker ./molecule_ranker
COPY deployment/scripts/entrypoint.sh /usr/local/bin/molecule-ranker-entrypoint
COPY deployment/scripts/wait_for_db.py /usr/local/bin/molecule-ranker-wait-for-db

RUN pip install --no-cache-dir . \
    && chmod 0755 /usr/local/bin/molecule-ranker-entrypoint \
    && chmod 0755 /usr/local/bin/molecule-ranker-wait-for-db

USER molecule-ranker

VOLUME ["/data/artifacts", "/data/storage", "/data/projects"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3).read()"

ENTRYPOINT ["/usr/local/bin/molecule-ranker-entrypoint"]
CMD ["web"]
