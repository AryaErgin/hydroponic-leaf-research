# Public Docker Official Images, pinned to registry manifest-list digests.
# Verified anonymously against registry-1.docker.io on 2026-11-15.
FROM node:24.11.0-bookworm-slim@sha256:76d0ed0ed93bed4f4376211e9d8fddac4d8b3fbdb54cc45955696001a3c91152 AS client
WORKDIR /client
COPY src/firebase_preview/client/package.json src/firebase_preview/client/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/firebase_preview/client/sdk-entry.js ./
RUN ./node_modules/.bin/esbuild sdk-entry.js --bundle --minify --format=esm --platform=browser --outfile=firebase-sdk.js

FROM python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HOME=/app/.runtime/huggingface \
    OMP_NUM_THREADS=2 \
    MKL_NUM_THREADS=2
WORKDIR /app
COPY requirements-web.txt requirements-firebase-preview.txt requirements-local-constraints.txt requirements-model-cpu.txt requirements-model-common.txt ./
RUN python -m pip install --only-binary=:all: -c requirements-local-constraints.txt -r requirements-web.txt -r requirements-firebase-preview.txt \
    && python -m pip install --only-binary=:all: -r requirements-model-cpu.txt \
    && python -m pip install --only-binary=:all: -r requirements-model-common.txt \
    && python -m pip check
COPY src/ ./src/
COPY configs/ ./configs/
# Only context.py may construct the build context. It installs the authorized
# bundle from the immutable model-assets policy; no dataset/photos/history.
COPY models/ ./models/
COPY --from=client /client/firebase-sdk.js ./src/firebase_preview/static/firebase-sdk.js
RUN groupadd --gid 10001 hydroponic \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin hydroponic \
    && mkdir -p /app/.runtime \
    && chown 10001:10001 /app/.runtime \
    && python -m src.local_setup.model_assets verify --project-root /app
USER 10001:10001
# Real promoted-model inference is a build gate as well as a runtime startup
# gate. Procedural fixture only; this is no biological validation claim.
RUN python -c "from pathlib import Path; from src.local_setup.diagnostics import model_smoke; assert model_smoke(Path('/app'),'cpu')['status']=='PASSED'"
EXPOSE 8080
CMD ["python", "-m", "src.cloud_instance.runtime"]
