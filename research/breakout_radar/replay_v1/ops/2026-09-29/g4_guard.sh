#!/bin/bash
# Radar replay guard (2026-09-29). Stops the replay when free disk falls below 12 GB, available memory
# below 6 GiB, at 14:30 UTC, or when no replay process is left. replay.py was renamed beforehand, so the
# orchestrator's retry fails at once instead of restarting a segment from its first day.
LOG=/content/logs/guard.log
PAT='replay_v1/scripts/[r]eplay\.py'
i=0
while true; do
  avail=$(df -B1 --output=avail /content | tail -1 | tr -d ' ')
  mem=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
  n=$(pgrep -f "$PAT" | wc -l)
  reason=""
  [ "$avail" -lt 12000000000 ] && reason="disk avail ${avail}B"
  [ "$mem" -lt 6 ] && reason="memory avail ${mem}GiB"
  [ "$(date -u +%Y%m%d%H%M)" -ge 202609291430 ] && reason="time limit 14:30 UTC"
  [ "$n" -eq 0 ] && reason="no replay process left"
  [ -n "$reason" ] && break
  [ $((i % 10)) -eq 0 ] && echo "$(date -u +%H:%M:%SZ) disk_avail=$((avail/1000000000))GB mem_avail=${mem}GiB procs=$n" >> $LOG
  i=$((i+1)); sleep 60
done
echo "$(date -u +%H:%M:%SZ) STOP ($reason)" >> $LOG
pids=$(pgrep -f "$PAT")
[ -n "$pids" ] && kill -TERM $pids
for k in $(seq 1 60); do [ -z "$(pgrep -f "$PAT")" ] && break; sleep 5; done
left=$(pgrep -f "$PAT"); [ -n "$left" ] && { echo "SIGKILL $left" >> $LOG; kill -KILL $left; }
echo "$(date -u +%H:%M:%SZ) stopped ($reason)" | tee -a $LOG > /content/STOPPED
