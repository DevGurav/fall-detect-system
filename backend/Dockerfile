# syntax=docker/dockerfile:1
#
# Fall Guardian cloud gateway — multi-stage build.
#
# The system runs local-first (uvicorn on the host + Postgres/Redis from
# docker-compose.yml, fronted by an ngrok tunnel for a physical phone), so this
# image is not required to run the project. It is kept for a reproducible build and
# as the seam for a future managed re-deploy: `cd backend && docker build -t fall-guardian .`
# then `docker run`. Build context is this directory (backend/). The builder resolves
# dependencies from pyproject.toml — there is no requirements.txt; pyproject is the
# single source of dependency truth — into an isolated virtualenv, and the slim
# runtime stage copies only that venv + the app code, so no build toolchain or pip
# ships in the final image.

############################
# Stage 1 — builder
############################
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Compilers for any sdist-only wheels (asyncpg, bcrypt) on slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
# pyproject + README (referenced by pyproject) + the package itself, then install.
# Installing `.` pulls the runtime dependencies declared in pyproject.toml.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

############################
# Stage 2 — runtime
############################
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    PATH="/opt/venv/bin:$PATH"

# onnxruntime needs libgomp at runtime; no build toolchain in the final image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# Application code + Alembic (run `alembic upgrade head` before serving — see RUN.md).
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

# Model location: the served cloud-detection ONNX is committed at
# app/model/cloud_detector.onnx (+ .meta.json) and is copied above with the app
# package — that is the artifact CloudDetector loads by default. The ml/artifacts/cloud/
# directory holds *training* outputs (the fp32 .pt checkpoint, eval npz, channel
# stats) and lives outside this build context, so it is intentionally not baked in.
# To serve a different artifact, mount it and point FG_MODEL_PATH at it; this empty
# mount point exists so such a volume has a home.
RUN mkdir -p /app/ml/artifacts/cloud && chown -R appuser:appuser /app

USER appuser
EXPOSE 8080

# In-container liveness check (any fronting proxy / orchestrator can also probe /health).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/health').status==200 else 1)"

# Honour $PORT (set by the host platform if any); default 8080 for `docker run`.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
