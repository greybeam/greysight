from auto_savings.sharding import owns_tenant


def test_partition_is_stable_and_disjoint():
    ids = [f"org-{i}" for i in range(50)]
    r0 = {t for t in ids if owns_tenant(t, num_replicas=3, replica_index=0)}
    r1 = {t for t in ids if owns_tenant(t, num_replicas=3, replica_index=1)}
    r2 = {t for t in ids if owns_tenant(t, num_replicas=3, replica_index=2)}
    assert r0 | r1 | r2 == set(ids)     # every tenant owned once
    assert r0 & r1 == set() and r1 & r2 == set() and r0 & r2 == set()


def test_fixed_tenants_map_to_locked_shard_indexes():
    # Locks in the stable sha256(tenant_id)[:8]-bytes-big-endian % num_replicas
    # hash (finding #23) — computed once from the real implementation and
    # hard-coded here so a salted/randomized-hash implementation would fail
    # this test even though test_partition_is_stable_and_disjoint above would
    # still pass (it only checks disjointness/coverage within one run).
    expected = {
        "org-alpha": 0,
        "org-beta": 0,
        "org-gamma": 2,
        "org-delta": 1,
        "org-epsilon": 0,
    }
    for tenant_id, expected_idx in expected.items():
        for idx in range(3):
            assert owns_tenant(tenant_id, num_replicas=3, replica_index=idx) == (
                idx == expected_idx
            )
