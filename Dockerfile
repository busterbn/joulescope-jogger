# Joulescope power logger
# Built with uv for a reproducible, locked dependency set.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# Runtime libs the Joulescope driver may dlopen for USB enumeration.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libudev1 libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (cached layer), then the app code.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY power_logger.py ./

CMD ["uv", "run", "--no-dev", "python", "power_logger.py"]
