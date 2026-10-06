import os
import sys

sys.path.insert(0, "/Users/binhvc/qc-agent")
from prompt_toolkit.history import InMemoryHistory

from cli.repl import SlashCommandCompleter, prompt_box

# Push the prompt to the bottom of the screen, like a REPL session with scrollback.
filler = int(os.environ.get("FILLER", "0"))
for i in range(filler):
    print(f"filler line {i:02d}")

history = InMemoryHistory()
completer = SlashCommandCompleter()
rounds = int(os.environ.get("ROUNDS", "1"))
for _ in range(rounds):
    print("RESULT:" + prompt_box("test", history, completer))
