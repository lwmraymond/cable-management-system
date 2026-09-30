"""No database, credentials, network, external references or user filesystem paths."""

import base64
import json
import resource
import sys

from app.exceptions import ValidationError
from app.services.cad_geometry import MAX_FILE_BYTES


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
    # macOS does not implement useful DATA/AS limits. The parent also monitors
    # resident memory and wall time; this process caps CPU and output file size.
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024,) * 2)
    raw = sys.stdin.buffer.read(MAX_FILE_BYTES + 1)
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise ValidationError("CAD file size limit exceeded")
    if len(sys.argv) > 2 and sys.argv[2] == "export":
        document = json.loads(raw)
        if sys.argv[1] == "dxf":
            from app.services.cad_dxf import export_dxf

            output = export_dxf(document)
        elif sys.argv[1] == "ifc":
            from app.services.cad_ifc import export_ifc

            output = export_ifc(document)
        else:
            raise ValidationError("Unsupported CAD format")
        if len(output) > MAX_FILE_BYTES:
            raise ValidationError("CAD file exceeds 8 MiB; export a smaller room")
        return {"file": base64.b64encode(output).decode("ascii")}
    if sys.argv[1] == "dxf":
        from app.services.cad_dxf import parse_dxf

        value = parse_dxf(raw)
    elif sys.argv[1] == "ifc":
        from app.services.cad_ifc import parse_ifc

        value = parse_ifc(raw)
    else:
        raise ValidationError("Unsupported CAD format")
    return value


if __name__ == "__main__":
    try:
        result = main()
    except ValidationError as error:
        result = {"error": str(error)}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
