"""Dump the emulated screen after every resize step, and compare our box against a
plain prompt_toolkit PromptSession (control)."""
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

COLS, ROWS = 100, 24
CPR = re.compile(r"\x1b\[6n")
STEPS = ((70, 18), (120, 30), (55, 14), (88, 24))
CHILD = sys.argv[1]


def set_winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def drain(master, screen, stream, budget):
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


def dump(label, screen):
    print(f"--- {label} ({screen.columns}x{screen.lines}) ---")
    for i, line in enumerate(screen.display):
        line = line.rstrip()
        if line and "filler" not in line:
            print(f"{i:3d}|{line}")


master, slave = pty.openpty()
set_winsize(slave, COLS, ROWS)
screen = pyte.Screen(COLS, ROWS)
stream = pyte.Stream(screen)

proc = subprocess.Popen(
    [sys.executable, "-u", os.path.join(os.path.dirname(os.path.abspath(__file__)), CHILD)],
    stdin=slave,
    stdout=slave,
    stderr=slave,
    env={**os.environ, "TERM": "xterm-256color", "PYTHONUNBUFFERED": "1", "FILLER": str(ROWS - 2), "ROUNDS": "1"},
    cwd="/Users/binhvc/qc-agent",
)
os.close(slave)

drain(master, screen, stream, 8.0)
os.write(master, b"hello")
drain(master, screen, stream, 1.0)
dump("initial", screen)

for cols, rows in STEPS:
    set_winsize(master, cols, rows)
    screen.resize(rows, cols)
    proc.send_signal(signal.SIGWINCH)
    drain(master, screen, stream, 1.2)
    dump(f"after resize {cols}x{rows}", screen)

os.write(master, b"\r")
drain(master, screen, stream, 1.5)
try:
    proc.wait(timeout=5)
except subprocess.TimeoutExpired:
    proc.kill()
dump("final", screen)
