"""Step 2: keep ONE bash process open and send it many commands.

This is what the bash tool needs: `cd` and `export` must carry over between
the model's calls. Runs on the host with hard-coded, harmless commands only.
Run: python3 learn/bash-tool/02_persistent_shell.py
"""

import queue
import subprocess
import threading

# Printed after every command so we know where its output ends.
MARKER = "__COMMAND_DONE__"


class BashSession:
    def __init__(self) -> None:
        # Popen starts bash and returns immediately; bash keeps running and
        # waits for commands on its stdin, like a terminal you type into.
        self.process = subprocess.Popen(
            ["bash"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        # Reading stdout blocks until a line arrives, which would freeze us
        # forever if a command hangs. So a background thread does the reading
        # and puts each line on a queue; we wait on the queue with a timeout.
        self.lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._read_forever, daemon=True).start()

    def _read_forever(self) -> None:
        for line in self.process.stdout:  # ends when bash exits
            self.lines.put(line)

    def run(self, command: str, timeout_seconds: float = 5) -> tuple[str, int]:
        """Send `command` to the running bash. Return (output, exit_code)."""
        # After the command, print the marker and $? (the command's exit code).
        # The leading \n makes sure the marker starts on its own line even if
        # the command's output didn't end with a newline.
        self.process.stdin.write(f"{command}\nprintf '\\n{MARKER} %s\\n' $?\n")
        self.process.stdin.flush()  # actually send it now, don't leave it in a buffer

        output_lines = []
        while True:
            try:
                line = self.lines.get(timeout=timeout_seconds)
            except queue.Empty:
                self.process.kill()  # the shell is stuck; the only fix is a new one
                return f"Command timed out after {timeout_seconds}s; shell must be restarted", -1
            if line.startswith(MARKER):
                exit_code = int(line.split()[1])
                output = "".join(output_lines)
                # Drop the \n we added before the marker.
                return output.removesuffix("\n"), exit_code
            output_lines.append(line)


shell = BashSession()
examples = [
    "cd /tmp",
    "pwd",  # /tmp: the cd from the previous call was remembered
    "export GREETING=hi",
    "echo $GREETING",  # hi: environment variables carry over too
    "ls /does-not-exist",
    "sleep 10",  # times out; the session is now dead
]
for command in examples:
    output, exit_code = shell.run(command, timeout_seconds=2)
    print(f"$ {command}")
    print(f"  exit code: {exit_code}")
    print(f"  output:    {output!r}")
