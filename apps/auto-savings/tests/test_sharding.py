from auto_savings.sharding import owns_tenant


def test_single_replica_owns_all():
    assert owns_tenant("any", num_replicas=1, replica_index=0) is True


def test_partition_is_stable_and_disjoint():
    ids = [f"org-{i}" for i in range(50)]
    r0 = {t for t in ids if owns_tenant(t, num_replicas=3, replica_index=0)}
    r1 = {t for t in ids if owns_tenant(t, num_replicas=3, replica_index=1)}
    r2 = {t for t in ids if owns_tenant(t, num_replicas=3, replica_index=2)}
    assert r0 | r1 | r2 == set(ids)     # every tenant owned once
    assert r0 & r1 == set() and r1 & r2 == set() and r0 & r2 == set()
