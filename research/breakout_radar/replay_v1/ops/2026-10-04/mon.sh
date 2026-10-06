#!/bin/bash
# One 30-minute arm: poll both G4s every 5 minutes; print a line only for events worth acting on
# (unreachable machine, guard STOP/SIGTERM, a failed step, the continuation done). Lines already
# printed are remembered in mon_seen so re-arms do not repeat them.
S=/private/tmp/claude-501/-Users-admin-Downloads-Claude/99ab3c9e-6f66-4900-9bd7-3b470a45eea9/scratchpad
touch $S/mon_seen
emit() { grep -qxF "$1" $S/mon_seen || { echo "$1"; echo "$1" >> $S/mon_seen; }; }
for i in 1 2 3 4 5 6; do
  for pair in "radarc3 2"; do
    set -- $pair; s=$1; m=$2
    out=$(~/.local/bin/colab --auth=oauth2 download -s $s /content/logs/agent.log $S/mon_agent_$m.log 2>&1 || true)
    case "$out" in *lost*|*"not found"*|*rror*) echo "$(date -u +%H:%M)Z $s unreachable: $(echo "$out" | tail -c 160)";; esac
    ~/.local/bin/colab --auth=oauth2 download -s $s /content/logs/throttle_$m.log $S/mon_guard_$m.log >/dev/null 2>&1 || true
    grep -hE "done 30_cont|FAILED" $S/mon_agent_$m.log 2>/dev/null | while read -r l; do emit "$s agent: $l"; done
    grep -hE "STOP files|SIGTERM|EMERGENCY" $S/mon_guard_$m.log 2>/dev/null | while read -r l; do emit "$s guard: $l"; done
  done
  sleep 300
done
