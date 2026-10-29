from pathlib import Path

from coding_agent.sandbox import DEFAULT_IMAGE, SandboxConfig, docker_run_args


def args_for(tmp_path: Path, *, allow_network: bool = False) -> list[str]:
    (tmp_path / ".git").mkdir(exist_ok=True)
    config = SandboxConfig(allow_network=allow_network)
    return docker_run_args("coding-agent-test", tmp_path, config, uid=501, gid=20)


def flag_values(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, arg in enumerate(args) if arg == flag]


def test_runs_detached_as_the_host_user_with_privileges_dropped(tmp_path: Path):
    args = args_for(tmp_path)

    assert args[:3] == ["docker", "run", "--detach"]
    assert flag_values(args, "--user") == ["501:20"]
    assert flag_values(args, "--cap-drop") == ["ALL"]
    assert flag_values(args, "--security-opt") == ["no-new-privileges"]
    assert "--privileged" not in args


def test_network_is_off_unless_allowed(tmp_path: Path):
    assert flag_values(args_for(tmp_path), "--network") == ["none"]
    assert flag_values(args_for(tmp_path, allow_network=True), "--network") == []


def test_resources_are_limited(tmp_path: Path):
    args = args_for(tmp_path)

    assert flag_values(args, "--memory")
    assert flag_values(args, "--cpus")
    assert flag_values(args, "--pids-limit")


def test_only_the_workspace_is_mounted_with_git_read_only(tmp_path: Path):
    args = args_for(tmp_path)

    assert flag_values(args, "--volume") == [
        f"{tmp_path}:/workspace",
        f"{tmp_path / '.git'}:/workspace/.git:ro",
    ]
    assert flag_values(args, "--workdir") == ["/workspace"]


def test_no_host_environment_is_passed_in(tmp_path: Path):
    args = args_for(tmp_path)

    assert flag_values(args, "--env") == ["HOME=/tmp"]
    assert "-e" not in args
    assert "--env-file" not in args


def test_default_image_is_pinned_by_digest(tmp_path: Path):
    args = args_for(tmp_path)

    assert DEFAULT_IMAGE.startswith("python:3.12-slim@sha256:")
    assert args[-3:] == [DEFAULT_IMAGE, "sleep", "infinity"]
