FROM python:3.13-slim

# Install uv from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies before copying source — better layer caching on redeploy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and install the project itself
COPY . .
RUN uv sync --frozen --no-dev

ENV PYTHONUTF8=1

EXPOSE 8767

CMD ["uv", "run", "serve"]
