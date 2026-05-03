FROM python:3.10-slim-bookworm

ENV UV_INSTALL_DIR=/usr/local/bin
ADD https://astral.sh/uv/install.sh /uv-install.sh
RUN sh /uv-install.sh && rm /uv-install.sh

WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY shared /app/shared
COPY security /app/security
COPY channels/signal /app/channels/signal

RUN uv sync --package signal-adapter --frozen --no-dev

ENV PATH=/app/.venv/bin:$PATH

# The signal-adapter is a polling worker, not an HTTP server. main.py
# runs the poll loop forever; healthz is exposed on whatever port the
# operator chooses (no port bound by default).
CMD ["python", "-m", "signal_adapter"]
