# Base container
FROM docker.io/library/python:3.12-slim-bookworm@sha256:541d45d3d675fb8197f534525a671e2f8d66c882b89491f9dda271f4f94dcd06 AS base

# Build container
FROM base AS build

RUN rm -f /etc/apt/apt.conf.d/docker-clean \
  && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache
RUN --mount=target=/var/lib/apt/lists,type=cache,sharing=locked --mount=target=/root/.cache/pip,type=cache,sharing=locked \
  apt-get update -q \
  && apt-get install -y -q --no-install-recommends \
  gcc \
  python3-dev \
  && python3 -m pip install --upgrade \
  pip \
  setuptools \
  wheel

RUN python -m venv /venv
ENV PATH=/venv/bin:$PATH

COPY requirements.txt .
RUN --mount=target=/root/.cache/pip,type=cache,sharing=locked \
  python3 -m pip install --requirement requirements.txt

# Output container
FROM base

ARG VERSION
ENV VERSION=${VERSION}

RUN useradd -m appuser \
  && mkdir /app \
  && chown -R appuser:appuser /app

USER appuser

COPY --from=build /venv /venv
ENV PATH=/venv/bin:$PATH

COPY --chown=appuser:appuser . /app

WORKDIR /app

CMD ["bash", "-c", "gunicorn main:api --bind 0.0.0.0:8080 --proxy-protocol --workers 4 --worker-class uvicorn.workers.UvicornWorker"]
