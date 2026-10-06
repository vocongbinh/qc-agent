#!/usr/bin/env bash
# Verify the inline prompt box against a real terminal emulator (tmux) while the
# window is resized, the way a user drags the window edge.
set -u

SESSION=qcbox$$
CHILD=${1:-box_child.py}
cd /Users/binhvc/qc-agent

tmux kill-session -t "$SESSION" 2>/dev/null
tmux new-session -d -s "$SESSION" -x 100 -y 24 \
  "FILLER=22 ROUNDS=1 python -u .scratch/$CHILD"
tmux set-option -t "$SESSION" -g status off
sleep 6
tmux send-keys -t "$SESSION" "hello"
sleep 1

dump() {
  echo "--- $1 ---"
  tmux capture-pane -p -t "$SESSION" | grep -v 'filler line' | sed '/^$/d'
}
dump "initial 100x24"

for size in 70x18 120x30 55x14 88x24 64x20; do
  w=${size%x*}; h=${size#*x}
  tmux resize-window -t "$SESSION" -x "$w" -y "$h"
  sleep 0.8
  dump "after resize $size"
done

echo "=== duplicate check (final screen) ==="
tmux capture-pane -p -t "$SESSION" | grep -c '\[TEST\]' | sed 's/^/top-border rows: /'
tmux capture-pane -p -t "$SESSION" | grep -c '^\s*\u2570' | sed 's/^/bottom-border rows: /'
tmux kill-session -t "$SESSION" 2>/dev/null
