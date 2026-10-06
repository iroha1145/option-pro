#!/bin/bash
# Manual stop on the user's request (2026-09-29): same sequence as g4_guard.sh.
LOG=/content/logs/guard.log
PAT='replay_v1/scripts/[r]eplay\.py'
pkill -f 'bash /content/g4_[g]uard.sh' && echo "$(date -u +%H:%M:%SZ) guard stopped for a manual stop" >> $LOG
echo "$(date -u +%H:%M:%SZ) STOP (user request)" >> $LOG
pids=$(pgrep -f "$PAT"); echo "SIGTERM to $(echo $pids | wc -w) replay processes"
[ -n "$pids" ] && kill -TERM $pids
for k in $(seq 1 60); do [ -z "$(pgrep -f "$PAT")" ] && break; sleep 5; done
left=$(pgrep -f "$PAT"); [ -n "$left" ] && { echo "SIGKILL $left" >> $LOG; kill -KILL $left; }
echo "$(date -u +%H:%M:%SZ) stopped (user request)" | tee -a $LOG > /content/STOPPED
echo "STOPPED: $(cat /content/STOPPED)"; echo "replay procs left: $(pgrep -f "$PAT" | wc -l)"; echo "disk avail GB: $(df -B1G --output=avail /content | tail -1)"
tail -2 /content/logs/run_full.log
