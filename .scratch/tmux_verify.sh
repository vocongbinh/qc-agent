#!/usr/bin/env bash
# Full capture: completion popup + resize + multiple prompt rounds.
set -u
SESSION=qcver$$
cd /Users/binhvc/qc-agent

tmux kill-session -t "$SESSION" 2>/dev/null
tmux new-session -d -s "$SESSION" -x 100 -y 24 \
  "FILLER=6 ROUNDS=3 python -u .scratch/box_child.py"
tmux set-option -t "$SESSION" -g status off
sleep 6

dump() { echo "--- $1 ---"; tmux capture-pane -p -t "$SESSION" | sed '/^$/d'; }

tmux send-keys -t "$SESSION" "/mo"
sleep 1
dump "completion popup at 100x24"

tmux resize-window -t "$SESSION" -x 70 -y 18
sleep 1
dump "popup after resize 70x18"

tmux send-keys -t "$SESSION" Enter
sleep 1
tmux send-keys -t "$SESSION" Enter
sleep 1
dump "round 1 submitted (/model), round 2 open"

tmux resize-window -t "$SESSION" -x 80 -y 24
sleep 1
tmux send-keys -t "$SESSION" "second request" Enter
sleep 1
tmux resize-window -t "$SESSION" -x 120 -y 26
sleep 1
dump "round 3 open at 120x26 (min-width check done at 80)"

tmux kill-session -t "$SESSION" 2>/dev/null
