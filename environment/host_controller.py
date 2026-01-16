"""Host controller abstraction for VM leasing on a single machine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class VMInfo:
    """Descriptor for a VM managed by the host controller.

    Expected inputs:
        - vm_id: Unique VM identifier on this host.
        - vm_ip: IP or hostname reachable by clients.
        - agent_port: Port of the guest agent HTTP server.

    Expected outputs:
        - Instances are immutable data carriers.
    """

    vm_id: str
    vm_ip: str
    agent_port: int


class HostController:
    """Tracks free/leased VMs and leases them for rollouts.

    Expected inputs:
        - vms: Initial list of VMInfo objects managed by this host.
        - max_concurrent: Optional cap on concurrent leases.

    Expected outputs:
        - acquire_vm() returns a VMInfo for the lease.
        - release_vm() returns a bool or raises on failure.
    """

    def __init__(self, vms: List[VMInfo], max_concurrent: Optional[int] = None) -> None:
        self.vms = vms
        self.max_concurrent = max_concurrent

    def acquire_vm(self) -> VMInfo:
        """Lease a VM for a rollout.

        Expected inputs:
            - None.

        Expected outputs:
            - VMInfo for the leased VM.
        """
        raise NotImplementedError

    def release_vm(self, vm_id: str) -> bool:
        """Release a previously leased VM back to the pool.

        Expected inputs:
            - vm_id: Identifier of the VM to release.

        Expected outputs:
            - True if released; False or raise on failure.
        """
        raise NotImplementedError

    def list_vms(self) -> Dict[str, VMInfo]:
        """Return a snapshot of VM lease status.

        Expected inputs:
            - None.

        Expected outputs:
            - Dict keyed by vm_id with VMInfo values.
        """
        raise NotImplementedError

