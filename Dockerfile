# syntax=docker/dockerfile:1

# ---- 1. Build CSS with the Tailwind standalone CLI (no Node needed) ----
# Base images are pinned by digest (update deliberately: docker buildx imagetools inspect <image>).
FROM debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3 AS css
ARG TAILWIND_VERSION=v4.3.3
ARG TAILWIND_SHA256_X64=dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a
ARG TAILWIND_SHA256_ARM64=55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN case "${TARGETARCH:-amd64}" in \
      amd64) arch=x64; sum="${TAILWIND_SHA256_X64}" ;; arm64) arch=arm64; sum="${TAILWIND_SHA256_ARM64}" ;; \
      *) echo "Unsupported arch ${TARGETARCH}" && exit 1 ;; \
    esac \
    && curl -fsSL -o /usr/local/bin/tailwindcss \
       "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-${arch}" \
    && echo "${sum}  /usr/local/bin/tailwindcss" | sha256sum -c - \
    && chmod +x /usr/local/bin/tailwindcss
WORKDIR /src
COPY app ./app
RUN tailwindcss -i app/static/src/app.css -o app/static/css/app.css --minify

# ---- 2. Runtime ----
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS runtime
LABEL org.opencontainers.image.title="VFAS" \
      org.opencontainers.image.description="VietNam Free Accounting Software – free, open-source accounting for Vietnamese SMEs (TT133)" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/nviai/vfas"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    TZ=Asia/Ho_Chi_Minh \
    XDG_CACHE_HOME=/tmp/.cache

# WeasyPrint needs Pango; fonts with full Vietnamese diacritics (Liberation Serif ~ Times New Roman).
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
      fonts-liberation2 fonts-dejavu-core fontconfig tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY --from=css /src/app/static/css/app.css ./app/static/css/app.css
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN useradd --system --uid 1000 --home /app vfas \
    && mkdir -p /data && chown -R vfas:vfas /data \
    && chmod +x /usr/local/bin/entrypoint.sh
USER vfas

VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

ENTRYPOINT ["entrypoint.sh"]
