"""Repro: inline prompt box leaves duplicated border rows when the terminal is resized.

Emulates the real terminal closely:
- answers CPR (ESC[6n) queries, like a real terminal;
- draws the status-line symbols 2 columns wide (VS Code / iTerm behaviour for
  East-Asian-Ambiguous glyphs) while prompt_toolkit's wcwidth assumes 1;
- pushes the prompt to the bottom of the screen so redraws have to scroll.
"""
import fcntl
import os
import pty
import re
import signal
import struct
import subprocess
import sys
import termios
import time

import pyte
import pyte.screens

WIDE_GLYPHS = ""
_wcwidth = pyte.screens.wcwidth
pyte.screens.wcwidth = lambda ch: 2 if ch in WIDE_GLYPHS else _wcwidth(ch)

COLS, ROWS = 100, 24
FILLER = ROWS - 2
CPR = re.compile(r"\x1b\[6n")


def set_winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def drain(master: int, screen: pyte.Screen, stream: pyte.Stream, budget: float) -> None:
    deadline = time.time() + budget
    os.set_blocking(master, False)
    while time.time() < deadline:
        try:
            data = os.read(master, 65536)
        except (BlockingIOError, OSError):
            time.sleep(0.02)
            continue
        if not data:
            time.sleep(0.02)
            continue
        text = data.decode("utf-8", "replace")
        stream.feed(text)
        for _ in CPR.findall(text):
            os.write(master, f"\x1b[{screen.cursor.y + 1};{screen.cursor.x + 1}R".encode())


master, slave = pty.openpty()
set_winsize(slave, COLS, ROWS)

screen = pyte.Screen(COLS, ROWS)
stream = pyte.Stream(screen)

proc = subprocess.Popen(
    [sys.executable, "-u", os.path.join(os.path.dirname(os.path.abspath(__file__)), "box_child.py")],
    stdin=slave,
    stdout=slave,
    stderr=slave,
    env={
        **os.environ,
        "TERM": "xterm-256color",
        "PYTHONUNBUFFERED": "1",
        "FILLER": str(FILLER),
        "ROUNDS": "1",
    },
    cwd="/Users/binhvc/qc-agent",
)
os.close(slave)

drain(master, screen, stream, 8.0)
os.write(master, b"hello")
drain(master, screen, stream, 1.0)

for cols, rows in ((70, 18), (120, 30), (55, 14), (88, 24)):
    set_winsize(master, cols, rows)
    screen.resize(rows, cols)
    proc.send_signal(signal.SIGWINCH)
    drain(master, screen, stream, 1.2)

os.write(master, b"\r")
drain(master, screen, stream, 1.5)
try:
    proc.wait(timeout=5)
except subprocess.TimeoutExpired:
    proc.kill()

lines = [line.rstrip() for line in screen.display]
top = sum(1 for line in lines if "[TEST]" in line)
bottom = sum(1 for line in lines if line.strip().startswith("\u2570"))
prompts = sum(1 for line in lines if line.startswith("\u2502 > "))
print("=== final screen ===")
for i, line in enumerate(lines):
    if line:
        print(f"{i:2d}|{line}")
print("=== counts ===")
print(f"top-border rows: {top}  bottom-border rows: {bottom}  prompt rows: {prompts}")
print("VERDICT:", "DUPLICATED" if max(top, bottom, prompts) > 1 else "CLEAN")
