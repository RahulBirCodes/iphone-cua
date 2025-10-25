import base64
import paramiko
import time
import uuid
import ssh_config
import json
from PIL import Image
import io


class InvalidArgException(Exception):
    """Raised for invalid args"""

    pass


class EnvException(Exception):
    """Raised for simulation errors"""

    pass


class IPhoneSim:
    def __init__(self, ssh_config: dict, device_name: str = "iPhone-17-Pro"):
        self.main_id = None
        self.current_clone_id = None
        self.device_name = device_name
        self.aspect_ratio = None

        print("Connecting to remote host...")
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh_client.connect(**ssh_config)
        self.sftp_client = self.ssh_client.open_sftp()
        print("Connection successful.")

    def _run_command(self, command: str, timeout: int = 30) -> str:
        try:
            # run gui control commands through remote agent
            if "osascript" in command:
                og_cmd = command
                enc_cmd = base64.b64encode(command.encode("utf-8")).decode("utf-8")
                data = {"command": enc_cmd}
                command = (
                    f"curl -s -X POST http://127.0.0.1:9000/run \
                      -H \"Content-Type: application/json\" \
                      -d '{json.dumps(data)}'"
                )
                print(f"executing command: {command} through remote agent")
                print(f"original command: {og_cmd}")
                curl_stdin, curl_stdout, curl_stderr = self.ssh_client.exec_command(command, timeout=timeout)
                curl_exit_code = curl_stdout.channel.recv_exit_status()
                curl_stdout_str = curl_stdout.read().decode("utf-8").strip()
                curl_stderr_str = curl_stderr.read().decode("utf-8").strip()
                if curl_exit_code != 0:
                    err_msg = f"curl command to remote agent failed with exit code {curl_exit_code}. stderr: {curl_stderr_str} stdout: {curl_stdout_str}"
                    print(err_msg)
                    raise EnvException(err_msg)
                data = json.loads(curl_stdout_str)
                exit_code = data["returncode"]
                stdout_str = data["stdout"]
                stderr_str = data["stderr"]
                command = og_cmd
            else:
                print(f"executing: {command}")
                stdin, stdout, stderr = self.ssh_client.exec_command(
                    command, timeout=timeout
                )
                exit_code = stdout.channel.recv_exit_status()
                stdout_str = stdout.read().decode("utf-8").strip()
                stderr_str = stderr.read().decode("utf-8").strip()
            if exit_code != 0:
                err_msg = f"command: {command} failed with exit code: {exit_code}. stderr: {stderr_str} stdout: {stdout_str}"
                print(err_msg)
                raise EnvException(err_msg)
            return stdout_str
        except EnvException as e:
            raise e
        except Exception as e:
            raise EnvException(f"error while executing command: {command}")

    def _run_gui_command(self, command: str, timeout: int = 30, max_retries: int = 10) -> str:
        """Utility function to run GUI commands with polling so account for WindowServer settling"""
        attempt = 0
        while True:
            try:
                self._run_command(command, timeout=timeout)
                break
            except EnvException as e:
                print(f"  - Attempt {attempt + 1} failed: {e}. GUI not ready yet or permissions issue. Retrying...")
                if attempt == max_retries - 1:
                    print("  - FAILED: Max retries reached. Click command failed.")
                    raise
                time.sleep(1)
            attempt += 1


    def _setup_main(self):
        print("\n--- Initializing main sim ---")
        self._run_command("open -a Simulator")
        time.sleep(5)
        self._run_command("xcrun simctl shutdown all")
        self._run_command("xcrun simctl delete all")
        main_id = self._run_command(
            f"xcrun simctl create 'main' com.apple.CoreSimulator.SimDeviceType.{self.device_name}"
        ).strip()
        if not main_id:
            raise RuntimeError("Failed to create master simulator.")
        self.main_id = main_id
        bezel_script = """
           tell application "System Events" to tell process "Simulator"
               set frontmost to true
               if (value of attribute "AXMenuItemMarkChar" of menu item "Show Device Bezels" of menu "Window" of menu bar 1) is "✓" then
                   click menu item "Show Device Bezels" of menu "Window" of menu bar 1
               end if
           end tell
           """
        self._run_command(f"osascript -e '{bezel_script}'")
        self._run_command(f"xcrun simctl bootstatus {main_id} -b", timeout=300)
        init_b64_obs = self._take_screenshot_b64(self.main_id)
        obs_bytes = base64.b64decode(init_b64_obs.encode("utf-8"))
        obs_file = io.BytesIO(obs_bytes)
        with Image.open(obs_file) as img:
            aspect_ratio = img.size[0] / img.size[1]
            print("sim_window aspect ratio:", aspect_ratio)
            self.aspect_ratio = aspect_ratio
        self._run_command(f"xcrun simctl shutdown {main_id}")
        print("--- Main sim successfully initialized ---")

    def _take_screenshot_b64(self, sim_id = None) -> str:
        remote_path = f"/tmp/{uuid.uuid4()}.png"
        sim_id = sim_id or self.current_clone_id
        self._run_command(
            f"xcrun simctl io {sim_id} screenshot {remote_path}"
        )
        obs_b64 = ""
        try:
            with self.sftp_client.open(remote_path) as f:
                obs_bytes = f.read()
                obs_b64 = base64.b64encode(obs_bytes).decode("utf-8")
        finally:
            self._run_command(f"rm {remote_path}")
        return obs_b64

    def _new(self) -> str:
        print("\n--- Cloning new sim ---")
        if self.current_clone_id is not None:
            raise RuntimeError(
                f"A simulator clone ({self.current_clone_id}) is already running."
            )
        elif self.main_id is None:
            self._setup_main()
        self._run_command("open -a Simulator")
        new_clone_id = self._run_command(
            f"xcrun simctl clone 'main' 'current_sim'"
        ).strip()
        if not new_clone_id:
            raise RuntimeError("Failed to create a new simulator clone.")
        self._run_command(f"xcrun simctl bootstatus {new_clone_id} -b", timeout=180)
        self.current_clone_id = new_clone_id
        print(f"--- New sim {self.current_clone_id} successfully cloned ---")
        return self._take_screenshot_b64()

    def _get_window_geometry(self) -> tuple[int, int, int, int]:
        """Gets the position and size of the Simulator window. Includes height of menu bar."""
        script = """
        tell application "System Events" to tell process "Simulator"
            set frontmost to true
            if not (exists (window 1)) then return "error: no window"
            set {x, y} to position of window 1
            set {w, h} to size of window 1
            return "" & x & "," & y & "," & w & "," & h
        end tell
        """
        result = self._run_command(f"osascript -e '{script}'")
        if not result or "error" in result:
            print("ERROR:", result)
            raise EnvironmentError("Could not get simulator window geometry.")
        try:
            return tuple(map(int, result.split(",")))
        except ValueError:
            raise EnvironmentError(f"Unexpected format for window geometry: {result}")

    def _translate_coords(self, norm_x: float, norm_y: float) -> tuple[int, int]:
        if not (0.0 <= norm_x <= 1.0 and 0.0 <= norm_y <= 1.0):
            raise InvalidArgException(
                f"Normalized coordinates ({norm_x}, {norm_y}) are out of the [0.0, 1.0] bounds."
            )
        win_x, win_y, win_w, win_h = self._get_window_geometry()
        expected_content_h = win_w / self.aspect_ratio
        title_bar_h = win_h - expected_content_h
        content_origin_x = win_x
        content_origin_y = win_y + title_bar_h # Offset by the calculated title bar height
        abs_x = content_origin_x + (norm_x * win_w)
        abs_y = content_origin_y + (norm_y * expected_content_h)
        return int(abs_x), int(abs_y)

    def _handle_tap(self, action: dict):
        coords = self._translate_coords(action['x'], action['y'])
        command = f"osascript -e 'tell application \"System Events\" to click at {{{coords[0]}, {coords[1]}}}'"
        self._run_gui_command(command)

    def _handle_swipe(self, action: dict):
        start_coords = self._translate_coords(action['start_x'], action['start_y'])
        end_coords = self._translate_coords(action['end_x'], action['end_y'])
        command = f"osascript -e 'tell application \"System Events\" to tell process \"Simulator\" to click and drag from {{{start_coords[0]}, {start_coords[1]}}} to {{{end_coords[0]}, {end_coords[1]}}} with duration 0.5'"
        self._run_gui_command(command)

    def _handle_type_text(self, action: dict):
        text = action['text'].replace("'", "\\'").replace('"', '\\"')
        command = f"osascript -e 'tell application \"System Events\" to keystroke \"{text}\"'"
        self._run_gui_command(command)

    def _handle_go_home(self, action: dict):
        command = "osascript -e 'tell application \"System Events\" to key code 102 using {shift down, command down}'"
        self._run_gui_command(command)



    def _cleanup(self):
        if not self.current_clone_id:
            return
        sim_id = self.current_clone_id
        print(f"Cleaning up clone ID: {sim_id}")
        self._run_command(f"xcrun simctl shutdown {sim_id}", timeout=20)
        self._run_command(f"xcrun simctl delete {sim_id}", timeout=20)
        self.current_sim_id = None

    def reset(self):
        self._cleanup()
        self._new()
        # allow the new sim to settle in window server + springboard
        print("sleeping to allow springboard to settle")
        time.sleep(70)

    def close(self):
        print("\n --- Closing connections and sims ---")
        self.sftp_client.close()
        self.ssh_client.close()
        print("--- Closed connections ---")


if __name__ == "__main__":
    sim_controller = IPhoneSim(ssh_config=ssh_config.SSH_CONFIG)
    sim_controller.reset()

    # test internal handlers
    sim_controller._handle_tap({"x": 0.4, "y": 0.5})