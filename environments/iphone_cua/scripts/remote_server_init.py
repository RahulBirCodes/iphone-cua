import argparse
import time
from pathlib import Path
import paramiko


REMOTE_PATH = "~/remote_server.py"
PLIST_PATH = "~/Library/LaunchAgents/com.remote.agent.plist"


def ensure_remote_agent(host: str, user: str, key_path: str):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, username=user, key_filename=key_path)
        sftp = ssh.open_sftp()
        remote_server_path = REMOTE_PATH.replace("~", f"/Users/{user}")
        try:
            sftp.stat(remote_server_path)
            print("remote_server.py already exists on remote VM.")
        except FileNotFoundError:
            print("Uploading remote_server.py to remote VM...")
            local_path = Path(__file__).parent.parent / "remote_server.py"
            sftp.put(local_path.as_posix(), f"/Users/{user}/remote_server.py")

        venv_path = f"/Users/{user}/agent_env"
        stdin, stdout, stderr = ssh.exec_command(f"test -d {venv_path} && echo 'exists' || echo 'not exists'")
        venv_status = stdout.read().decode().strip()
        if venv_status != "exists":
            print("Creating virtual environment on remote VM...")
            ssh.exec_command(f"python3 -m venv {venv_path}")
        else:
            print("Virtual environment already exists on remote VM.")
        print("VENV PATH:", venv_path)

        # Use venv python explicitly
        stdin, stdout, stderr = ssh.exec_command(f"{venv_path}/bin/python3 -c 'import sys; print(sys.executable)'")
        python_path = stdout.read().decode().strip()
        print("python:", python_path)

        print("Installing Flask in venv...")
        ssh.exec_command(f"{venv_path}/bin/pip install flask")

        plist_remote_path = f"/Users/{user}/Library/LaunchAgents/com.remote.agent.plist"
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key> <string>com.remote.agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/{user}/agent_env/bin/python3</string>
    <string>/Users/{user}/remote_server.py</string>
  </array>
  <key>RunAtLoad</key> <true/>
  <key>KeepAlive</key> <true/>
  <key>StandardOutPath</key> <string>/tmp/agent.out</string>
  <key>StandardErrorPath</key> <string>/tmp/agent.err</string>
</dict>
</plist>"""

        try:
            sftp.stat(plist_remote_path)
            print("LaunchAgent plist already exists.")
        except FileNotFoundError:
            print("Creating LaunchAgent plist...")
            ssh.exec_command(f"mkdir -p /Users/{user}/Library/LaunchAgents")
            with sftp.file(plist_remote_path, "w") as f:
                f.write(plist_content)
            ssh.exec_command(f"launchctl load {plist_remote_path}")

        print("Checking if agent is running...")
        stdin, stdout, stderr = ssh.exec_command(
            "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/health || true"
        )
        http_code = stdout.read().decode().strip()
        if http_code == "200":
            print("Agent is already running.")
        else:
            print("Starting agent manually...")
            ssh.exec_command(f"nohup {venv_path}/bin/python3 /Users/{user}/remote_server.py >/tmp/agent.out 2>&1 &")
            time.sleep(3)
            stdin, stdout, stderr = ssh.exec_command(
                "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/health || true"
            )
            out = stdout.read().decode().strip()
            if out == "200":
                print("Agent started successfully.")
            else:
                print("Failed to verify agent startup.")
                print(out)

        sftp.close()
        print("Setup complete.")
    except Exception as e:
        print(f"Error: {e}")
    ssh.close()


def main():
    parser = argparse.ArgumentParser(
        description="Initialize and start remote Flask agent on macOS VM."
    )
    parser.add_argument("--host", required=True, help="VM hostname or IP address")
    parser.add_argument("--user", required=True, help="Username for SSH connection")
    parser.add_argument(
        "--key-path", required=True, help="Path to SSH private key file"
    )
    args = parser.parse_args()
    ensure_remote_agent(args.host, args.user, args.key_path)


if __name__ == "__main__":
    main()
