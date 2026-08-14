# Stage 1: build the React bundle. static/dist is gitignored, so it must be built
# here rather than copied from the build context.
FROM node:22-slim AS frontend

WORKDIR /build/static

COPY static/package.json static/package-lock.json ./
RUN npm ci

COPY static/tsconfig.json static/tsconfig.node.json static/vite.config.ts ./
COPY static/placeholder-album.png ./
COPY static/ts ./ts
RUN npm run build

# Stage 2: the application image
FROM python:3.13

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    # The image sets no locale, so Python would default to ASCII and choke on the
    # Cyrillic track names the moment anything opens a file without an explicit encoding.
    PYTHONUTF8=1

# Install dependencies first so this layer is cached independently of source changes
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --group prod

# Put the project venv on PATH so uwsgi/python resolve without `uv run`
ENV PATH="/app/.venv/bin:$PATH"

# Copy the Python scripts
COPY *.py ./

# Jinja templates and the built frontend the app serves (Flask is configured with
# static_folder='static/dist'). Without these every page 500s with TemplateNotFound.
COPY templates/ ./templates/
COPY --from=frontend /build/static/dist ./static/dist

# Declare the port so reverse proxies (Traefik/Caddy) can discover the backend
EXPOSE 8001

# Probe through the WSGI stack so a wedged worker is reported unhealthy, not just a
# dead process. Uses python rather than curl so it does not depend on the base image
# shipping one.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=4).status == 200 else 1)"

# --lazy-apps: load the app in each worker AFTER forking. Loading pre-fork leaves the
#   APScheduler and logging locks held in the children, which can deadlock workers.
# --enable-threads: the playlist create/merge endpoints run work in threading.Thread.
# CMD ["python", "app.py"]
CMD ["uwsgi", "--http", "0.0.0.0:8001", "--master", "--lazy-apps", "--enable-threads", "-p",  "4",  "-w", "app:app"]
