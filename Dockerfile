FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
RUN uv pip install --no-cache-dir --no-deps . --python .venv/bin/python


FROM python:3.12-slim

RUN useradd --create-home --shell /bin/bash hive
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER hive
ENTRYPOINT ["hive-vault"]
