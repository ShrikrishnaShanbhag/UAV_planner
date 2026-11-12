FROM python:3.12-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir uv

COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src
COPY backend/scenarios ./scenarios
RUN uv sync --frozen --no-dev --python-preference only-system

EXPOSE 8000
CMD [".venv/bin/uvicorn", "uavplan.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
