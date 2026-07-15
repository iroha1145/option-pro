FROM python:3.12.13-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b

ARG APP_VERSION=dev
ARG APP_COMMIT=unknown

LABEL org.opencontainers.image.title="Optix Pro" \
      org.opencontainers.image.source="https://github.com/iroha1145/option-pro" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${APP_COMMIT}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp/optix-home \
    XDG_CACHE_HOME=/tmp/optix-cache \
    FRONTEND_MANIFEST_PATH=/app/frontend-manifest.txt \
    FRONTEND_MANIFEST_REQUIRED=1 \
    APP_VERSION="${APP_VERSION}" \
    APP_COMMIT="${APP_COMMIT}"

WORKDIR /app/backend

COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --require-hashes -r requirements.txt \
    && addgroup --system app \
    && adduser --system --ingroup app --home /home/app app \
    && install -d -o app -g app -m 0700 /data \
    && install -d -o root -g root -m 0755 /licenses

# Application files remain root-owned and read-only to the runtime user.
COPY backend/app ./app
COPY config/personal.toml /app/config/personal.toml
COPY contracts /app/contracts
COPY frontend /app/frontend
COPY THIRD_PARTY_NOTICES.md /licenses/THIRD_PARTY_NOTICES.md
COPY third_party/BreakoutAnalysis-LICENSE /licenses/BreakoutAnalysis-LICENSE
RUN python -c "from pathlib import Path; root = Path('/app/frontend'); files = sorted(path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file()); Path('/app/frontend-manifest.txt').write_text('\\n'.join(files) + '\\n', encoding='utf-8')"

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3).read()"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-server-header"]
