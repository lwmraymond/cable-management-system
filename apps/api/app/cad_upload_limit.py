"""Bound CAD multipart bytes before the framework spools any whole upload."""

from starlette.responses import JSONResponse
from starlette.formparsers import MultiPartException

from app.services.cad_geometry import MAX_FILE_BYTES


class UploadTooLarge(MultiPartException):
    def __init__(self):
        super().__init__("CAD upload exceeds the 8 MiB file / bounded multipart limit")


class CadUploadLimitMiddleware:
    # Allow bounded multipart envelope overhead, independently of file content.
    limit = MAX_FILE_BYTES + 64 * 1024

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not scope.get("path", "").endswith("/scene/cad/imports")
        ):
            return await self.app(scope, receive, send)
        count = 0

        async def bounded_receive():
            nonlocal count
            message = await receive()
            count += len(message.get("body", b""))
            if count > self.limit:
                raise UploadTooLarge()
            return message

        async def bounded_send(message):
            # Request.form converts MultiPartException into 400 after closing
            # its spooled files. Retain that cleanup and report the proper 413.
            if count > self.limit and message["type"] == "http.response.start":
                message = {**message, "status": 413}
            await send(message)

        try:
            lengths = [
                value for key, value in scope.get("headers", []) if key.lower() == b"content-length"
            ]
            if lengths and (not lengths[0].isdigit() or len(lengths) != 1):
                await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(
                    scope, receive, send
                )
                return
            if lengths and int(lengths[0]) > self.limit:
                raise UploadTooLarge()
            await self.app(scope, bounded_receive, bounded_send)
        except UploadTooLarge:
            await JSONResponse(
                {"detail": "CAD upload exceeds the 8 MiB file / bounded multipart limit"},
                status_code=413,
            )(scope, receive, send)
