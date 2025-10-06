#!/bin/bash
#source /root/.bashrc
which python
cd /app
pip install -r /requirements.txt
python load_playlist.py