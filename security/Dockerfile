FROM python:3.10-slim-bookworm AS base

# Non-root user for runtime.
RUN useradd --system --home /home/stevens --create-home stevens

# uv for dependency management — mirrors host toolchain.
ENV UV_INSTALL_DIR=/usr/local/bin
ADD https://astral.sh/uv/install.sh /uv-install.sh
RUN sh /uv-install.sh && rm /uv-install.sh

WORKDIR /app

# Copy workspace members needed to resolve the security package.
COPY pyproject.toml uv.lock /app/
COPY shared /app/shared
COPY agents /app/agents
COPY channels /app/channels
COPY security /app/security

RUN uv sync --package stevens-security --frozen --no-dev

ENV STEVENS_SECURITY_SOCKET=/run/stevens/security.sock \
    STEVENS_SECURITY_AUDIT_DIR=/var/lib/stevens/audit \
    STEVENS_SECURITY_SECRETS=/var/lib/stevens/secrets \
    STEVENS_SECURITY_AGENTS=/app/security/policy/agents.yaml \
    STEVENS_SECURITY_POLICY=/app/security/policy/capabilities.yaml \
    PATH=/app/.venv/bin:$PATH

# Mounted at runtime by compose; chmod is set by the socket server.
RUN mkdir -p /run/stevens /var/lib/stevens/audit /var/lib/stevens/secrets \
    && chown -R stevens:stevens /run/stevens /var/lib/stevens

USER stevens

ENTRYPOINT ["python", "-m", "stevens_security"]
