from __future__ import annotations

import hashlib


def owns_tenant(tenant_id: str, *, num_replicas: int, replica_index: int) -> bool:
    if num_replicas <= 1:
        return True
    digest = hashlib.sha256(tenant_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % num_replicas == replica_index
