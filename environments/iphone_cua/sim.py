import base64
import paramiko
import time
import uuid
import sys
import textwrap
import os
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
        # let window server settle
        # time.sleep(15)
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
        time.sleep(30)  # allow sim ui to settle/load in
        self.current_clone_id = new_clone_id
        print(f"--- New sim {self.current_clone_id} successfully cloned ---")
        return self._take_screenshot_b64()

    def _get_window_geometry(self) -> tuple[int, int, int, int]:
        """Uses AppleScript to get the position and size of the Simulator window. Includes height of menu bar."""
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
        # ... (rest of the logic is the same)
        geometry = self._get_window_geometry()
        win_x, win_y, win_w, win_h = geometry
        scale = win_w / self.screen_width
        expected_content_h = self.screen_height * scale
        title_bar_h = win_h - expected_content_h
        content_origin_x = win_x
        content_origin_y = win_y + title_bar_h
        abs_x = content_origin_x + (norm_x * win_w)
        abs_y = content_origin_y + (norm_y * expected_content_h)
        abs_x = max(win_x, min(abs_x, win_x + win_w - 1))
        abs_y = max(win_y, min(abs_y, win_y + win_h - 1))
        return int(abs_x), int(abs_y)

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

    def close(self):
        print("\n --- Closing connections and sims ---")
        self.sftp_client.close()
        self.ssh_client.close()
        print("--- Closed connections ---")


if __name__ == "__main__":
    sim_controller = IPhoneSim(ssh_config=ssh_config.SSH_CONFIG)
    print("RESETTING CONTROLLER....")
    sim_controller.reset()
    print("finished reset, going to sleep")
    time.sleep(120)
    geometry = sim_controller._get_window_geometry()
    if geometry:
        win_x, win_y, win_w, win_h = geometry

        # Calculate the absolute center of the window
        center_x = win_x + (win_w / 2)
        center_y = win_y + (win_h / 2)

        print(f"Calculated window center at: ({int(center_x)}, {int(center_y)})")
        print(f"Current aspect ratio of the simulator window: {sim_controller.aspect_ratio}")

        click_center_command = f"osascript -e 'tell application \"Simulator\" to activate' -e 'tell application \"System Events\" to click at {{{int(center_x) - 10}, {int(center_y) - 10}}}'"
        # # click_center_command = f"osascript -e 'tell application \"Simulator\" to activate' -e 'tell application \"System Events\" to click at {{{214}, {700}}}'"
        # sim_controller._run_command(click_center_command)
        sim_controller._run_gui_command(click_center_command)
        # attempt = 0
        # max_retries = 20
        # while True:
        #     try:
        #         print(f"  - Attempt {attempt + 1} to execute click command...")
        #         sim_controller._run_command(click_center_command)  # Attempt the click
        #         print("  - SUCCESS: Click command executed without error.")
        #         click_successful = True
        #         break  # Exit the loop if successful
        #     except EnvException as e:
        #         # Catch the specific error from _run_command (includes -25204)
        #         print(f"  - Attempt {attempt + 1} failed: {e}. GUI not ready yet or permissions issue. Retrying...")
        #         if attempt == max_retries - 1:
        #             print("  - FAILED: Max retries reached. Click command failed.")
        #             raise  # Re-raise the last exception if all retries fail
        #         time.sleep(1)  # Wait before the next attempt







    #
    #     print(
    #         "\nSUCCESS: A 'click' command was sent to the calculated center of the simulator window."
    #     )
    #     print(
    #         "Please check your VM's screen to visually validate the mouse position and click effect."
    #     )
    #     print("Pausing for 10 seconds before cleanup...")
    #     time.sleep(10)
    # else:
    #     print("Could not get window geometry, skipping validation.")

    #
    #
    # print("\n--- Fully Automated Golden Master Setup ---")
    # print("OPENING SIMULATOR...")
    # sim_controller._run_command("open -a Simulator")
    # time.sleep(5)
    #
    # print("SHUT DOWN AND DELETE ALL OPEN SIMULATORS...")
    # sim_controller._run_command("xcrun simctl shutdown all")
    # sim_controller._run_command("xcrun simctl delete all")
    # master_id = sim_controller._run_command(
    #     f"xcrun simctl create '{sim_controller.main_sim_name}' com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro").strip()
    # if not master_id: raise RuntimeError("Failed to create master simulator.")
    # print("got master id: {}".format(master_id))
    #
    # bezel_script = """
    # tell application "System Events" to tell process "Simulator"
    #     set frontmost to true
    #     if (value of attribute "AXMenuItemMarkChar" of menu item "Show Device Bezels" of menu "Window" of menu bar 1) is "✓" then
    #         click menu item "Show Device Bezels" of menu "Window" of menu bar 1
    #     end if
    # end tell
    # """
    # print("RUNNING SCRIPT TO DISABLE SIMULATOR BEZELS...")
    # sim_controller._run_command(f"osascript -e '{bezel_script}'")
    #
    # print("WAIT FOR SIMULATOR TO FINISH BOOTING...")
    # sim_controller._run_command(f"xcrun simctl bootstatus {master_id} -b", timeout=300)
    # print("SHUTTING DOWN SIMULATOR...")
    # sim_controller._run_command(f"xcrun simctl shutdown {master_id}")
    # print("--- Golden Master setup complete. ---")
    #
    #
    #
    #
    # print("SETTING UP NEW SIMULATOR...")
    # if sim_controller.current_clone_id is not None:
    #     raise RuntimeError(
    #         f"A simulator clone ({sim_controller.current_clone_id}) is already running. Please call cleanup() before starting a new one.")
    #
    # print("ENSURING SIMULATOR IS OPEN...")
    # sim_controller._run_command("open -a Simulator")
    #
    # print(f"CREATING NEW CLONE...")
    # new_clone_id = sim_controller._run_command(f"xcrun simctl clone '{sim_controller.main_sim_name}' 'current_sim'").strip()
    # if not new_clone_id: raise RuntimeError("Failed to create a new simulator clone.")
    #
    # print(f"BOOTING UP NEW CLONE...")
    # sim_controller._run_command(f"xcrun simctl bootstatus {new_clone_id} -b", timeout=180)
    #
    # sim_controller.current_clone_id = new_clone_id
    #
    # print(f"Boot complete. Active simulator is now {sim_controller.current_clone_id}")
