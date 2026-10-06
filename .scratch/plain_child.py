"""Control: stock prompt_toolkit single-line inline prompt, same resize sequence."""
import os

from prompt_toolkit import PromptSession

for i in range(int(os.environ.get("FILLER", "0"))):
    print(f"filler line {i:02d}")

session = PromptSession()
print("RESULT:" + session.prompt("PLAINPROMPT> "))
