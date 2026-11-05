ARG PYTHON_BASE=3.15-slim

# ---- Stage 1: Builder ----
FROM python:${PYTHON_BASE} AS builder

ENV DEBIAN_FRONTEND=noninteractive

LABEL org.opencontainers.image.source="https://github.com/klappstuhlpy/percy"
LABEL org.opencontainers.image.description="Percy Discord Bot"
LABEL org.opencontainers.image.licenses="MPL2.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1 \
    \
    PYSETUP_PATH="/opt/pysetup" \
    VENV_PATH="/opt/pysetup/.venv" \
    \
    LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8

WORKDIR /project

# System dependencies (added build-essential + python dev headers!)
RUN apt-get update && apt-get install --no-install-recommends --no-install-suggests -y \
    locales \
    git \
    curl \
    build-essential \
    gcc \
    python3-dev \
    && echo "en_US.UTF-8 UTF-8" > /etc/locale.gen \
    && locale-gen \
    && update-locale LANG=en_US.UTF-8 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install poetry
RUN pip install poetry

# Copy dependency files
COPY poetry.lock pyproject.toml ./

ENV POETRY_VIRTUALENVS_IN_PROJECT=true

# Every release bumps `version` in pyproject.toml, which reruns this layer. The cache mount keeps
# downloads and built wheels, so that takes seconds and a dependency change only fetches what changed.
RUN --mount=type=cache,target=/root/.cache/pypoetry \
    poetry config virtualenvs.in-project true \
    && poetry install --only main --no-interaction --no-ansi

# ---- Stage 2: Final ----
FROM python:${PYTHON_BASE} AS final

ENV VENV_PATH="/app/.venv"
ENV PATH="$VENV_PATH/bin:$PATH"
ENV LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8

# The locale must exist in the image that runs the bot, not only in the builder:
# app/utils/formats.py formats numbers with en_US grouping. git reads the copied
# .git for the version string (config.get_full_version) and the `about` commit list.
RUN apt-get update && apt-get install --no-install-recommends --no-install-suggests -y locales git \
    && echo "en_US.UTF-8 UTF-8" > /etc/locale.gen \
    && locale-gen \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /project/.venv /app/.venv
# The bot runs with -O, so it needs .opt-1.pyc files: without them every new container recompiles
# everything at boot (about 2 s). Compiled here so tracebacks show the final /app paths.
RUN python -O -m compileall -q -j 0 /usr/local/lib/python3* /app/.venv

COPY . /app/
RUN python -O -m compileall -q -j 0 /app/app /app/config.py /app/main.py

# Python as PID 1 ignores SIGTERM, so `docker stop` waited 10 s and then killed it.
STOPSIGNAL SIGINT

CMD ["python", "-O", "main.py"]