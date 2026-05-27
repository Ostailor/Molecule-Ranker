from __future__ import annotations

import os

host = os.getenv("MOLECULE_RANKER_HOST", "127.0.0.1")
port = int(os.getenv("MOLECULE_RANKER_PORT", "8765"))
proxy_headers = True
forwarded_allow_ips = os.getenv("MOLECULE_RANKER_FORWARDED_ALLOW_IPS", "127.0.0.1")
timeout_keep_alive = int(os.getenv("MOLECULE_RANKER_KEEPALIVE_SECONDS", "30"))
limit_concurrency = int(os.getenv("MOLECULE_RANKER_UVICORN_LIMIT_CONCURRENCY", "100"))
