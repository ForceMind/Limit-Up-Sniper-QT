from __future__ import annotations

from app.quant.application_bootstrap import build_application_runtime


_APP_RUNTIME = build_application_runtime(globals(), app_file=__file__)
globals().update(_APP_RUNTIME.exports())
app = _APP_RUNTIME.app
