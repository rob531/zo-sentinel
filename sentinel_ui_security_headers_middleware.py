import os
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Match

_log = logging.getLogger(__name__)
_hdl = logging.FileHandler('/home/workspace/logs/sentinel_ui_security_headers.log')
_hdl.setLevel(logging.WARNING)
_fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
_hdl.setFormatter(_fmt)
_log.addHandler(_hdl)
_log.setLevel(logging.WARNING)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        return response


def install(app):
    app.add_middleware(SecurityHeadersMiddleware)

    async def openapi_endpoint(request):
        token = os.environ.get('SENTINEL_OPENAPI_TOKEN', '')
        if not token:
            _log.warning('OPENAPI_AUTH_ATTEMPT_WITHOUT_CONFIGURED_TOKEN path=%s client=%s',
                         request.url.path, request.client.host if request.client else 'unknown')
            return JSONResponse({'detail': 'Not authenticated'}, status_code=401)

        provided = request.headers.get('X-Sentinel-Token', '')
        if provided != token:
            _log.warning('OPENAPI_AUTH_FAILED_INVALID_TOKEN path=%s client=%s',
                         request.url.path, request.client.host if request.client else 'unknown')
            return JSONResponse({'detail': 'Not authenticated'}, status_code=401)

        schema = app.openapi()
        return JSONResponse(schema)

    app.add_route('/openapi.json', openapi_endpoint, methods=['GET'])