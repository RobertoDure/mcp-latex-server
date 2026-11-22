# Build the MCP server image. A TeX distribution is intentionally not included;
# install one in a derived image if you need compile_latex inside the container.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LATEX_SERVER_BASE_PATH=/workspace

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir .

WORKDIR /workspace

ENTRYPOINT ["mcp-latex-server"]
