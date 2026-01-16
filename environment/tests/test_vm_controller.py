from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict


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

    start_cmd = (
        f"cd {remote_dir} && nohup {args.python} run_vm_controller.py "
        f"--port {args.remote_port} > vm_controller.log 2>&1 & echo $!"
    )
    result = _run(ssh_base + [start_cmd])
    pid = result.stdout.strip()
    if not pid.isdigit():
        print("Failed to start remote vm_controller. Output:")
        print(result.stdout)
        print(result.stderr)
        return 1

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
        print("Resetting simulator (disable bezels + erase)...")
        _post_action(url, "reset", {})
        _prompt("Press Enter to continue to tap...", args.auto)

        print("Tap at center (0.5, 0.5)")
        print(_post_action(url, "tap", {"x": 0.5, "y": 0.5}))
        _prompt("Press Enter to continue to swipe...", args.auto)

        print("Swipe left-to-right")
        print(
            _post_action(
                url,
                "swipe",
                {"x1": 0.2, "y1": 0.5, "x2": 0.8, "y2": 0.5},
            )
        )
        _prompt("Press Enter to continue to type...", args.auto)

        print("Type text")
        print(_post_action(url, "type_text", {"text": "hello from vm_controller"}))
        _prompt("Press Enter to continue to go_home...", args.auto)

        print("Go home")
        print(_post_action(url, "go_home", {}))
        _prompt("Press Enter to continue to wait...", args.auto)

        print("Wait")
        print(_post_action(url, "wait", {"seconds": 1.0}))
        _prompt("Press Enter to observe...", args.auto)

        print("Observe")
        print(_post_action(url, "observe", {}))
        _prompt("Press Enter to finish...", args.auto)
    finally:
        tunnel.terminate()
        tunnel.wait(timeout=10)
        _run(ssh_base + [f"kill {pid}"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
