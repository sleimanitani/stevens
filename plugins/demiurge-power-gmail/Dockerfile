FROM python:3.12-slim

WORKDIR /app

# Install uv
RUN pip install --no-cache-dir uv

# Copy workspace root and the relevant packages
COPY pyproject.toml /app/pyproject.toml
COPY shared /app/shared
COPY channels/gmail /app/channels/gmail

# Install everything via the workspace
RUN cd /app && uv sync --package gmail-adapter --no-dev

# Put the venv's bin at the front of PATH so `uvicorn` resolves
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["uvicorn", "gmail_adapter.main:app", "--host", "0.0.0.0", "--port", "8080"]
