"""Optional native adapters. Untrusted parsing runs in a bounded, separate process."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from app.exceptions import ValidationError
from app.services.cad_geometry import MAX_FILE_BYTES


def capabilities():
    return {
        "dxf": {
            "available": importlib.util.find_spec("ezdxf") is not None,
            "profile": "R2013 / CMS-CAD-1",
        },
        "ifc": {
            "available": importlib.util.find_spec("ifcopenshell") is not None,
            "profile": "IFC4 Add2 TC1 / CMS-CAD-1",
        },
        "dwg": {
            "available": False,
            "reason": "未配置并验证免费的 DWG 转换适配器；本版本不读写 DWG。",
        },
    }


def require_format(format):
    if format not in {"dxf", "ifc"} or not capabilities()[format]["available"]:
        raise ValidationError("Native DXF/IFC dependencies are required; DWG is unavailable")


def export_file(format, manifest):
    payload = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode()
    result = _run_worker(format, payload, "export")
    return base64.b64decode(result["file"], validate=True)


def parse_file(format, raw):
    return _run_worker(format, raw, "parse")


def _run_worker(format, raw, mode):
    require_format(format)
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise ValidationError("CAD file must be between 1 byte and 8 MiB")
    with tempfile.TemporaryDirectory(prefix="cms-cad-parse-") as temporary:
        env = {
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_CACHE_HOME": temporary,
            "PATH": os.defpath,
            "LANG": "en_US.UTF-8",
        }
        try:
            with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output:
                source.write(raw)
                source.seek(0)
                process = subprocess.Popen(
                    [sys.executable, "-m", "app.services.cad_worker", format, mode],
                    stdin=source,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    cwd=temporary,
                    env=env,
                )
                started = time.monotonic()
                try:
                    while process.poll() is None:
                        if time.monotonic() - started > 20:
                            raise ValueError("parser timeout")
                        sample = subprocess.run(
                            ["/bin/ps", "-o", "rss=", "-p", str(process.pid)],
                            capture_output=True,
                            timeout=2,
                            check=False,
                        )
                        if sample.returncode and process.poll() is None:
                            raise ValueError("memory monitor unavailable")
                        rss = sample.stdout.strip()
                        if rss and int(rss) > 1024 * 1024:
                            raise ValueError("parser memory limit")
                        time.sleep(0.05)
                    if process.returncode != 0:
                        raise ValueError("parser failed")
                    output.seek(0)
                    payload = output.read(32 * 1024 * 1024 + 1)
                    if len(payload) > 32 * 1024 * 1024:
                        raise ValueError("result limit")
                    parsed = json.loads(payload)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
            if "error" in parsed:
                raise ValidationError(parsed["error"])
            return parsed
        except (subprocess.SubprocessError, ValueError, OSError) as exc:
            raise ValidationError("CAD parser failed or exceeded its resource/time limit") from exc
