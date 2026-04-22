FROM python:3.10-slim-bookworm

ENV UV_INSTALL_DIR=/usr/local/bin
ADD https://astral.sh/uv/install.sh /uv-install.sh
RUN sh /uv-install.sh && rm /uv-install.sh

WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY shared /app/shared
COPY channels/whatsapp-cloud /app/channels/whatsapp-cloud

RUN uv sync --package whatsapp-cloud-adapter --frozen --no-dev

ENV PATH=/app/.venv/bin:$PATH
EXPOSE 8082

CMD ["uvicorn", "whatsapp_cloud_adapter.main:app", "--host", "0.0.0.0", "--port", "8082"]
