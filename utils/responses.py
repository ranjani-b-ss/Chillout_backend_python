from fastapi.responses import JSONResponse


def handle_success_response(code: int, data=None, message: str = "Success"):
    if data is None:
        data = {}
    return JSONResponse(
        status_code=code,
        content={
            "status": "success",
            "code": code,
            "message": message,
            "data": data
        }
    )


def handle_error_response(code: int, error):
    if isinstance(error, Exception):
        error = str(error)
    return JSONResponse(
        status_code=code,
        content={
            "status": "failure",
            "code": code,
            "error": error
        }
    )
