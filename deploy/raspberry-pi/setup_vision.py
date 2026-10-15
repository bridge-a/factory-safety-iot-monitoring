"""Download the pinned model if needed, verify inference, optionally enable it."""
import argparse
from datetime import datetime
import hashlib
import io
from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from vision_detector import FireModel, MODEL_DIR, model_manifest, verified_model


def prepare(download=True):
    manifest = model_manifest()
    path = MODEL_DIR / manifest["file"]
    try:
        verified_model()
    except ValueError:
        if not download:
            raise
        print("Downloading pinned ONNX model (12 MB)...", flush=True)
        with urllib.request.urlopen(manifest["url"], timeout=120) as response:
            data = response.read(manifest["size"] + 1)
        if len(data) != manifest["size"] or hashlib.sha256(data).hexdigest() != manifest["sha256"]:
            raise ValueError("Downloaded model checksum mismatch; existing file was not overwritten")
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".download")
        temporary.write_bytes(data)
        temporary.replace(path)
    from PIL import Image
    model = FireModel()
    sample = io.BytesIO()
    Image.new("RGB", (1280, 720), (90, 90, 90)).save(sample, format="JPEG")
    model.infer(sample.getvalue())
    times = []
    for _ in range(3):
        start = time.perf_counter()
        model.infer(sample.getvalue())
        times.append(time.perf_counter()-start)
    print(f"Model verified: {model.name}; classes: fire=0, smoke=1; alarm: fire only")
    print(f"CPU decode + inference + draw: {sum(times)/len(times)*1000:.0f} ms average (synthetic frame; not accuracy validation)")


def enable():
    path = ROOT / "deploy/raspberry-pi/runtime.env"
    original = path.read_bytes()
    lines = original.decode("utf-8-sig").splitlines()
    backup = path.with_name("runtime.env.bak-vision-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    backup.write_bytes(original)
    backup.chmod(0o600)
    lines = [line for line in lines if line.strip().removeprefix("export ").split("=", 1)[0].strip() != "FACTORY_VISION_ENABLED"]
    lines.append("FACTORY_VISION_ENABLED=1")
    path.write_bytes(("\n".join(lines)+"\n").encode("utf-8"))
    path.chmod(0o600)
    print("Vision enabled: 2 FPS / confidence 0.55 / confirm 1.5s defaults.")
    print("Existing camera rotation, MQTT, mode, mail and custom vision settings retained.")
    print("Restart: bash deploy/raspberry-pi/run.sh")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--check-only", action="store_true", help="Verify local weights; do not download or edit config")
    args = parser.parse_args()
    if args.enable and args.check_only:
        parser.error("--enable and --check-only cannot be combined")
    try:
        prepare(download=not args.check_only)
        if args.enable:
            enable()
    except Exception as exc:
        print(f"Vision setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
