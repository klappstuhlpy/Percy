ARG PYTHON_BASE=3.12-slim

FROM python:${PYTHON_BASE} AS builder

ENV DEBIAN_FRONTEND=noninteractive

LABEL org.opencontainers.image.source=https://github.com/klappstuhlpy/percy
LABEL org.opencontainers.image.description="Percy Discord Bot"
LABEL org.opencontainers.image.licenses=MPL2.0

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
COPY . /project/
RUN apt-get update && aapt-get install --no-install-recommends --no-install-suggests -y \
    locales \
    git && \
    # locale fix
    echo "en_US.UTF-8 UTF-8" > /etc/locale.gen && \
    locale-gen && \
    update-locale LANG=en_US.UTF-8 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN curl -sSL https://install.python-poetry.org | python -
WORKDIR /app
COPY poetry.lock pyproject.toml ./

RUN poetry install -n --only main --no-root

COPY --from=builder /project/.venv/ /app/.venv
ENV PATH="$POETRY_HOME/bin:$VENV_PATH/bin:$PATH" \
COPY . /app/

CMD ["poetry", "run", "python", "-O", "main.py"]