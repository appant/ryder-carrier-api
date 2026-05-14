FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml ./
COPY src ./src
COPY sql ./src/ryder_carrier_api/sql
RUN pip install -e .

RUN useradd --create-home --shell /bin/bash app && chown -R app:app /app
USER app

# Default to running the trace job; Container App Jobs override with `command`.
# Valid args: trace | milestone | cleanup
ENTRYPOINT ["python", "-m", "ryder_carrier_api"]
CMD ["trace"]
