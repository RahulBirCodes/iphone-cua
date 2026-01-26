from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import base64
from pathlib import Path
from typing import Any, Callable, Dict, Tuple


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def _build_ssh_base(args: argparse.Namespace) -> list[str]:
    ssh_base = ["ssh", "-p", str(args.port)]
    if args.key:
        ssh_base.extend(["-i", args.key])
    ssh_base.append(f"{args.user}@{args.host}")
    return ssh_base


def _build_scp_base(args: argparse.Namespace) -> list[str]:
    scp_base = ["scp", "-P", str(args.port)]
    if args.key:
        scp_base.extend(["-i", args.key])
    return scp_base


def _write_runner(tmp_dir: Path) -> Path:
    runner = tmp_dir / "run_vm_controller.py"
    runner.write_text(
        "from __future__ import annotations\n"
        "\n"
        "import argparse\n"
        "from vm_controller import VMController\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    parser = argparse.ArgumentParser()\n"
        '    parser.add_argument("--host", default="0.0.0.0")\n'
        '    parser.add_argument("--port", type=int, default=8000)\n'
        "    args = parser.parse_args()\n"
        "    VMController(host=args.host, port=args.port).run()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
        encoding="utf-8",
    )
    return runner


def _resolve_remote_python(ssh_base: list[str], fallback: str) -> str:
    result = _run(ssh_base + ["which", fallback], check=False)
    path = result.stdout.strip()
    return path if path else fallback


def _write_plist(
    tmp_dir: Path, label: str, python_path: str, script_path: str, port: int
) -> Path:
    plist = tmp_dir / f"{label}.plist"
    plist.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>\n"""
        """<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"\n"""
        """ "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n"""
        """<plist version="1.0">\n"""
        """<dict>\n"""
        f"  <key>Label</key> <string>{label}</string>\n"
        "  <key>ProgramArguments</key>\n"
        "  <array>\n"
        f"    <string>{python_path}</string>\n"
        f"    <string>{script_path}</string>\n"
        f"    <string>--port</string>\n"
        f"    <string>{port}</string>\n"
        "  </array>\n"
        "  <key>RunAtLoad</key> <true/>\n"
        "  <key>KeepAlive</key> <true/>\n"
        "  <key>StandardOutPath</key> <string>/tmp/vm_controller.out</string>\n"
        "  <key>StandardErrorPath</key> <string>/tmp/vm_controller.err</string>\n"
        "</dict>\n"
        "</plist>\n",
        encoding="utf-8",
    )
    return plist


def _install_launch_agent(
    args: argparse.Namespace,
    ssh_base: list[str],
    scp_base: list[str],
    label: str,
    remote_dir: str,
) -> Tuple[str, str]:
    python_path = _resolve_remote_python(ssh_base, args.python)
    plist_remote_path = f"/Users/{args.user}/Library/LaunchAgents/{label}.plist"
    script_remote_path = f"{remote_dir}/run_vm_controller.py"
    _run(ssh_base + [f"mkdir -p /Users/{args.user}/Library/LaunchAgents"])
    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        plist = _write_plist(
            tmp_dir, label, python_path, script_remote_path, args.remote_port
        )
        _run(scp_base + [str(plist), f"{args.user}@{args.host}:{plist_remote_path}"])
    _run(ssh_base + [f"launchctl unload {plist_remote_path} >/dev/null 2>&1 || true"])
    _run(ssh_base + [f"launchctl load {plist_remote_path}"])
    return plist_remote_path, python_path


def _post_action(url: str, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.dumps({"action": action, "params": params}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def _prompt(msg: str, auto: bool) -> None:
    if auto:
        return
    input(msg)


def save_b64_image(b64_str: str, out_path: str) -> None:
    out_path = os.path.expanduser(out_path)

    if b64_str.startswith("data:"):
        b64_str = b64_str.split(",", 1)[1]

    img_bytes = base64.b64decode(b64_str)
    with open(out_path, "wb") as f:
        f.write(img_bytes)


def _with_remote_controller(
    args: argparse.Namespace,
    run_actions: Callable[[str, argparse.Namespace], None],
) -> int:
    ssh_base = _build_ssh_base(args)
    scp_base = _build_scp_base(args)

    local_vm_controller = Path(__file__).resolve().parents[1] / "vm_controller.py"
    if not local_vm_controller.exists():
        print(f"vm_controller.py not found at {local_vm_controller}")
        return 1

    remote_dir = args.remote_dir
    _run(ssh_base + [f"mkdir -p {remote_dir}"])

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        runner = _write_runner(tmp_dir)
        _run(
            scp_base
            + [
                str(local_vm_controller),
                f"{args.user}@{args.host}:{remote_dir}/vm_controller.py",
            ]
        )
        _run(
            scp_base
            + [
                str(runner),
                f"{args.user}@{args.host}:{remote_dir}/run_vm_controller.py",
            ]
        )

    label = "com.iphonecua.vmcontroller"
    plist_remote_path, _ = _install_launch_agent(
        args, ssh_base, scp_base, label, remote_dir
    )

    tunnel_cmd = ssh_base[:-1] + [
        "-N",
        "-L",
        f"{args.local_port}:127.0.0.1:{args.remote_port}",
        "-o",
        "ExitOnForwardFailure=yes",
        ssh_base[-1],
    ]
    tunnel = subprocess.Popen(
        tunnel_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    time.sleep(2)

    url = f"http://127.0.0.1:{args.local_port}/action"
    try:
        run_actions(url, args)
    finally:
        tunnel.terminate()
        tunnel.wait(timeout=10)
        _run(
            ssh_base + [f"launchctl unload {plist_remote_path} >/dev/null 2>&1 || true"]
        )

    return 0


def test_case_1(args: argparse.Namespace) -> int:
    def run_actions(url: str, args: argparse.Namespace) -> None:
        print("Resetting simulator (disable bezels + erase)...")
        _post_action(url, "reset", {})
        _prompt("Press Enter to continue to tap...", args.auto)

        print("Tap at (0.4, 0.4)")
        result = _post_action(url, "tap", {"x": 0.5, "y": 0.5})
        if result.get("error"):
            print(f"Error: {result['error']}")
        _prompt("Press Enter to continue to swipe...", args.auto)

        print("Swipe left-to-right")
        result = _post_action(
            url,
            "swipe",
            {"x1": 0.3, "y1": 0.5, "x2": 0.8, "y2": 0.5},
        )
        if result.get("error"):
            print(f"Error: {result['error']}")
        _prompt("Press Enter to continue to type...", args.auto)

        print("Type text")
        result = _post_action(url, "type_text", {"text": "hello from vm_controller"})
        if result.get("error"):
            print(f"Error: {result['error']}")
        _prompt("Press Enter to continue to go_home...", args.auto)

        save_b64_image(result["screenshot_b64"], "~/Desktop/debug_image.jpg")

        print("Go home")
        result = _post_action(url, "go_home", {})
        if result.get("error"):
            print(f"Error: {result['error']}")
        _prompt("Press Enter to continue to wait...", args.auto)

        print("Wait")
        result = _post_action(url, "wait", {"seconds": 1.0})
        if result.get("error"):
            print(f"Error: {result['error']}")
        _prompt("Press Enter to observe...", args.auto)

        print("Observe")
        result = _post_action(url, "observe", {})
        if result.get("error"):
            print(f"Error: {result['error']}")
        _prompt("Press Enter to finish...", args.auto)

    return _with_remote_controller(args, run_actions)


def test_case_2(args: argparse.Namespace) -> int:
    def run_actions(url: str, _args: argparse.Namespace) -> None:
        result = _post_action(url, "unknown_action", {})
        error = result.get("error")
        if not error:
            raise RuntimeError("expected error for unknown_action, but none returned")
        if not str(error).startswith("unknown_action"):
            raise RuntimeError(f"unexpected error for unknown_action: {error}")

    return _with_remote_controller(args, run_actions)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SSH into a VM, install vm_controller, and run manual action tests."
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--key", default=os.environ.get("VM_SSH_KEY"))
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--remote-dir", default="~/vm_controller_test")
    parser.add_argument("--remote-port", type=int, default=8000)
    parser.add_argument("--local-port", type=int, default=18000)
    parser.add_argument("--python", default="python3")
    parser.add_argument("--auto", action="store_true")
    args = parser.parse_args()

    print("Running test case 1...")
    if test_case_1(args) != 0:
        return 1

    print("Running test case 2...")
    return test_case_2(args)


if __name__ == "__main__":
    sys.exit(main())
