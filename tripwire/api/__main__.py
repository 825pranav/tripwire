"""Run the API: python -m tripwire.api"""

import uvicorn

from tripwire.common.config import load_settings
from tripwire.common.logs import setup_logging

if __name__ == "__main__":
    setup_logging("api")
    settings = load_settings()
    uvicorn.run(
        "tripwire.api.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=settings.api_port,
        log_config=None,
    )
