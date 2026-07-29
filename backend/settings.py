"""
Runtime application settings and mode configuration.

The primary switch here is DATA_SOURCE_MODE, which controls how the app sources
its data and which admin operations are available:

- "local"  (default): current behavior. Users/transactions live in Aerospike KV,
  counts are computed by KV scans, and the Admin panel can bulk load, inject
  transactions, compute features, run detection, and clear data.

- "remote": the dataset is bulk loaded EXTERNALLY into the Aerospike Graph at
  billion scale. KV holds only a small working set (flagged accounts + their
  features) written by an external pipeline. In this mode counts come from the
  Graph summary API, and app-side write/ingest operations are disabled.
"""

import os

VALID_MODES = ("local", "remote")


def _normalize_mode(raw: str | None) -> str:
    mode = (raw or "local").strip().lower()
    return mode if mode in VALID_MODES else "local"


DATA_SOURCE_MODE = _normalize_mode(os.environ.get("DATA_SOURCE_MODE"))


def is_remote_mode() -> bool:
    """True when the app runs against an externally bulk-loaded graph dataset."""
    return DATA_SOURCE_MODE == "remote"


def get_capabilities() -> dict:
    """
    Capability flags consumed by the frontend to adapt the UI and enforced by
    the backend to guard mutating endpoints.
    """
    remote = is_remote_mode()
    return {
        "bulkLoad": not remote,
        "injectTransactions": not remote,
        "computeFeatures": not remote,
        "runDetection": not remote,
        "clearData": not remote,
        "rtGeneration": not remote,
        # How the user browser is backed and where counts come from
        "browseSource": "graph" if remote else "kv",
        "statsSource": "graph_summary" if remote else "kv",
    }


def get_runtime_config() -> dict:
    """Full runtime config payload returned by GET /config."""
    return {
        "mode": DATA_SOURCE_MODE,
        "remote": is_remote_mode(),
        "capabilities": get_capabilities(),
    }
