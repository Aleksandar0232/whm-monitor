# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Unprivileged user. The Exim spool is read through a supplementary group
# (group_add in compose.yaml), never as root.
RUN groupadd --system --gid 10001 exporter \
    && useradd --system --uid 10001 --gid exporter --no-create-home --shell /usr/sbin/nologin exporter

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY whm_exporter ./whm_exporter
RUN python -m compileall -q whm_exporter

USER 10001:10001
EXPOSE 9877

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "-m", "whm_exporter", "--healthcheck"]

ENTRYPOINT ["python", "-m", "whm_exporter"]
