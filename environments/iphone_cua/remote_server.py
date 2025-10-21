from flask import Flask, request, jsonify
import subprocess
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "OK"}), 200


@app.route("/run", methods=["POST"])
def run_command():
    data = request.get_json()
    if not data or "command" not in data:
        logging.warning("Missing 'command' in request JSON")
        return jsonify({"error": "Missing 'command' in JSON"}), 400
    command = data["command"]
    logging.info(f"Received command: {command}")
    try:
        # Execute the command
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        response = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
        logging.info(f"Command executed with return code {result.returncode}")
        return jsonify(response), 200
    except Exception as e:
        logging.error(f"Error executing command: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=9000)
