# Bot image: Python 3.13, this repo's packages, Node, Claude Code, liteparse, qpdf.
# LibreOffice Writer is installed by default. `lit parse` uses `soffice` to read DOCX
# (HDFC statements). It is not a build arg.
FROM python:3.13-slim-bookworm

ARG NODE_VERSION=22.21.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        gnupg \
        libreoffice-writer \
        qpdf \
        tzdata \
        xz-utils \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-17 \
    && arch="$(dpkg --print-architecture)" \
    && case "$arch" in \
        arm64) node_arch=arm64 ;; \
        amd64) node_arch=x64 ;; \
        *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" \
        | tar -xJ -C /usr/local --strip-components=1 \
    && npm install -g @anthropic-ai/claude-code @llamaindex/liteparse \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV PYTHONPATH=/app \
    PATH="/app/.venv/bin:/usr/local/bin:${PATH}" \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 \
    FT_REPO_ROOT=/app \
    TZ=Asia/Kolkata

# ft_mcp is ticket 02. PYTHONPATH=/app makes `python -m ft_mcp` import it
# from the source tree copied below, including after that package changes
# and this image is rebuilt.
COPY pyproject.toml uv.lock README.md ./
COPY ft_db ./ft_db
COPY ft_bot ./ft_bot
COPY ft_mcp ./ft_mcp
COPY db ./db
COPY workspace ./workspace
COPY bot/agent ./bot/agent
COPY bot/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && uv sync --frozen --no-dev \
    && python -c "import ft_bot, ft_db, ft_mcp" \
    && command -v claude \
    && command -v lit \
    && command -v qpdf \
    && command -v soffice \
    && command -v pg_dump \
    && claude --version \
    && soffice --version

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "ft_bot"]
