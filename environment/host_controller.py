"""Host controller abstraction for VM leasing on a single machine."""

from __future__ import annotations

import threading
from typing import Dict, List, Optional, Set
from .types import VMInfo


class HostController:
    """Manages a pool of Tart VMs on a single host.

    # NOTE: Assumes Tart VMs are on a virtual network reachable from the host.

    Thread-safe: multiple agents can acquire/release VMs concurrently.

    Attributes:
        max_concurrent: Optional cap on concurrent leases.
    """

    def __init__(self, vms: List[VMInfo], max_concurrent: Optional[int] = None) -> None:
        assert len(vms) > 0, "HostController must be initialized with at least one VM."
        self._all_vms: Dict[str, VMInfo] = {vm.vm_id: vm for vm in vms}
        self._free: Set[str] = {vm.vm_id for vm in vms}
        self._leased: Set[str] = set()
        self._max_concurrent = max_concurrent
        lease_slots = len(vms)
        if max_concurrent is not None:
            lease_slots = min(lease_slots, max_concurrent)
        self._lease_slots = threading.BoundedSemaphore(lease_slots)
        self._state_lock = threading.Lock()

    def acquire_vm(
        self,
        timeout: Optional[float] = None,
    ) -> VMInfo:
        if not self._all_vms:
            raise RuntimeError("No VMs available")

        if timeout is None:
            acquired = self._lease_slots.acquire()
        else:
            acquired = self._lease_slots.acquire(timeout=timeout)

        if not acquired:
            raise RuntimeError("Unable to acquire VM")

        with self._state_lock:
            if not self._free:
                self._lease_slots.release()
                raise RuntimeError("No VMs available")
            vm_id = self._free.pop()
            self._leased.add(vm_id)
            return self._all_vms[vm_id]

    def release_vm(self, vm_id: str) -> bool:
        with self._state_lock:
            if vm_id not in self._leased:
                raise ValueError(f"VM {vm_id} is not currently leased")

            self._leased.remove(vm_id)
            self._free.add(vm_id)
        self._lease_slots.release()
        return True

    def list_vms(self) -> Dict[str, VMInfo]:
        with self._state_lock:
            return dict(self._all_vms)

    def get_free_count(self) -> int:
        with self._state_lock:
            return len(self._free)

    def get_leased_count(self) -> int:
        with self._state_lock:
            return len(self._leased)
