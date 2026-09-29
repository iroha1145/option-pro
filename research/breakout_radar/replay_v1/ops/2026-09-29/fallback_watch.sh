#!/bin/bash
# Backup launcher (2026-09-29): the queued kernel request for post_stop.py may have been lost with its
# client connection. Five minutes after the guard writes /content/STOPPED, start post_stop.py here
# unless it already started (post_stop.py itself also skips when its log exists).
until [ -f /content/STOPPED ]; do sleep 30; done
sleep 300
if [ ! -f /content/logs/post_stop.log ]; then
  echo "$(date -u +%H:%M:%SZ) fallback starts post_stop.py" >> /content/logs/guard.log
  cd /content && /usr/bin/python3 /content/post_stop.py > /content/logs/post_stop.fallback.out 2>&1
fi
