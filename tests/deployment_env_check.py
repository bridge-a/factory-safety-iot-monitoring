"""Check actual Bash startup with LF/CRLF configuration and a fake Python entry point."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    bash = str(git_bash) if os.name == "nt" and git_bash.exists() else shutil.which("bash")
    if not bash:
        raise SystemExit("Bash is required for this check.")
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("FACTORY_")}
    for newline in (b"\n", b"\r\n"):
        with tempfile.TemporaryDirectory(prefix="deploy-env-", dir=artifacts) as folder:
            project = Path(folder)
            deploy = project / "deploy/raspberry-pi"
            deploy.mkdir(parents=True)
            runner = deploy / "run.sh"
            runner.write_bytes((ROOT / "deploy/raspberry-pi/run.sh").read_bytes())
            python = project / ".venv/bin/python"
            python.parent.mkdir(parents=True)
            python.write_bytes(b'#!/usr/bin/env bash\nprintf "%s\\n" "$FACTORY_MODE" "$FACTORY_MQTT_HOST" "$FACTORY_ENV_TOPIC" "$FACTORY_MQTT_PASSWORD" "$1"\n')
            python.chmod(0o755)
            config = (b"# Local test values only\nFACTORY_MODE=live\n\n"
                      b"FACTORY_MQTT_HOST=127.0.0.1\nFACTORY_ENV_TOPIC=factory/workshop1/env\n"
                      b"FACTORY_MQTT_PASSWORD='test value with spaces'\n")
            config_path = deploy / "runtime.env"
            config_path.write_bytes(config.replace(b"\n", newline))
            # Capture inside the launching shell: Windows child environment
            # conversion can otherwise hide trailing CR characters in exports.
            capture = ('exec() { printf "%s\\n" "$FACTORY_MODE" "$FACTORY_MQTT_HOST" '
                       '"$FACTORY_ENV_TOPIC" "$FACTORY_MQTT_PASSWORD" "$2"; }; source "$1"')
            result = subprocess.run([bash, "-c", capture, "deployment-env-check", str(runner)],
                                    capture_output=True, env=env)
            assert result.returncode == 0, result.stderr.decode(errors="replace")
            assert result.stdout == (b"live\n127.0.0.1\nfactory/workshop1/env\n"
                                     b"test value with spaces\ndashboard.py\n"), repr(result.stdout)
            assert config_path.read_bytes() == config.replace(b"\n", newline), "Local config was modified"
    print("PASS: LF and CRLF load identical mode, broker, topic and quoted values; local config preserved; no actual Python/MQTT/SMTP started.")


if __name__ == "__main__":
    main()
