#!/bin/bash
# ffmpeg comes from the imageio-ffmpeg package, so nothing is installed at startup.
exec gunicorn --bind=0.0.0.0 --timeout 600 app:app
