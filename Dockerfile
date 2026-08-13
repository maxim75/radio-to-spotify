FROM python:3.13

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Install dependencies first so this layer is cached independently of source changes
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --group prod

# Put the project venv on PATH so uwsgi/python resolve without `uv run`
ENV PATH="/app/.venv/bin:$PATH"

# Copy the Python scripts
COPY *.py ./

# CMD ["python", "app.py"]
CMD ["uwsgi", "--http", "0.0.0.0:8001", "--master", "-p",  "4",  "-w", "app:app"]
