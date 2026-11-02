"""Local numeric/type diagnostics only: no exception message or frame locals."""
from pathlib import Path
import re
import traceback
import time
from sources import write_json


def failure_details(exc, stage, started=None):
    name = re.sub(r'[^A-Za-z0-9_]', '', type(exc).__name__)[:60]
    code = getattr(exc, 'code', None)
    if not isinstance(code, (int, str)) or not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', str(code)):
        code = name
    frames = traceback.extract_tb(exc.__traceback__)
    return {'stage': str(stage)[:180], 'error_type': name, 'code': code,
            'elapsed_seconds': round(time.monotonic()-started, 2) if started is not None else None,
            'time': time.time(), 'trace': [{'file': Path(f.filename).name, 'function': f.name, 'line': f.lineno} for f in frames[-8:]]}


def save_failure(folder, filename, exc, stage, started=None):
    detail = failure_details(exc, stage, started)
    try:write_json(Path(folder)/filename, detail)
    except OSError:pass  # Do not replace the original exception with logging I/O.
    return detail
