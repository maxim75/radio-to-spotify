#!/bin/bash
#source /root/.bashrc
export PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
which python
cd /app
pip install -r /requirements.txt
python load_playlist.py