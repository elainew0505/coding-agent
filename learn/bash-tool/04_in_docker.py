"""Step 4: run a bash session inside a locked-down Docker container.

NEEDS DOCKER running, and downloads the python:3.12-slim image the first time.
Run: python3 learn/bash-tool/04_in_docker.py
"""

import os
import shlex
import subprocess
import tempfile

workspace = tempfile.mkdtemp()  # a throwaway folder standing in for the user's repo
with open(os.path.join(workspace, "hello.txt"), "w") as f:
    f.write("hi from the host\n")

# 1. Start a container that just waits, so we can run commands in it.
container_id = subprocess.run(
    [
        "docker",
        "run",
        "--detach",  # run in the background and print the container id
        "--rm",  # delete the container when it stops
        "--network",
        "none",  # no internet
        "--cap-drop",
        "ALL",  # drop all special Linux privileges
        "--security-opt",
        "no-new-privileges",  # and never regain them (e.g. via sudo)
        "--user",
        f"{os.getuid()}:{os.getgid()}",  # same user as us, not root
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--pids-limit",
        "128",  # resource limits
        "--volume",
        f"{workspace}:/workspace",  # the ONLY host folder the container can see
        "--workdir",
        "/workspace",
        "python:3.12-slim",
        "sleep",
        "infinity",  # keep the container alive until we stop it
    ],
    capture_output=True,
    text=True,
    check=True,
).stdout.strip()

try:
    # 2. Start bash inside the container. `docker exec -i` connects our
    #    stdin/stdout to it, just like Popen(["bash"]) did in step 2.
    #    Our environment variables are not passed in, so ANTHROPIC_API_KEY stays out.
    shell = subprocess.Popen(
        ["docker", "exec", "-i", container_id, "bash"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    commands = [
        "cat hello.txt",  # visible: it's in the mounted workspace
        "echo written-in-container > from_container.txt",  # will show up on the host
        "ls /home",  # empty: the host's home folders are not in the container
        "id -u",  # our uid, not 0 (root)
        "env | grep -c ANTHROPIC || true",  # 0: the key isn't in the container
        # fails: no network
        "python -c 'import urllib.request as u; u.urlopen(\"https://example.com\")' 2>&1 | tail -1",
    ]
    # Echo each command before running it, so the output is easy to follow.
    # shlex.quote makes the text safe to put inside a bash command.
    script = "\n".join(f"echo {shlex.quote('$ ' + c)}; {c}" for c in commands)
    # To keep this step short we send everything at once (no marker trick):
    # communicate() writes the script, closes stdin so bash exits, and reads all output.
    output, _ = shell.communicate(script, timeout=30)
    print(output)

    with open(os.path.join(workspace, "from_container.txt")) as f:
        print("On the host, from_container.txt contains:", f.read().strip())
finally:
    # 3. Always clean up, even if something above failed.
    subprocess.run(["docker", "stop", "--time", "0", container_id], capture_output=True)
