#!/usr/bin/env python3
"""Shared connector-side per-user rate limiting."""

import threading
import time
from typing import Dict, List, Tuple


class ConnectorRateLimiter:
    """In-memory sliding-window limiter keyed by connector user identity."""

    def __init__(self):
        self.records: Dict[str, Dict[str, List[float]]] = {}
        self._lock = threading.Lock()

    def check(
        self, identity: str, bucket: str, max_requests: int, window: int
    ) -> Tuple[bool, int]:
        """Return whether the request is allowed and the retry wait in seconds."""
        now = time.time()
        with self._lock:
            buckets = self.records.setdefault(identity, {})
            timestamps = buckets.setdefault(bucket, [])
            timestamps[:] = [ts for ts in timestamps if now - ts < window]

            if len(timestamps) >= max_requests:
                retry_after = max(1, int(window - (now - timestamps[0])) + 1)
                return False, retry_after

            timestamps.append(now)
            return True, 0
