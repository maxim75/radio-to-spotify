#!/bin/bash
#source /root/.bashrc
# Dependencies are baked into /app/.venv at image build time (uv sync), so there
# is nothing to install here — just run against that environment.
export PATH=/app/.venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin
which python
cd /app
python load_playlist.py
