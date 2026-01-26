from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class HostInfo:
    host_id: str
    host_ip: str
    ssh_user: str
    ssh_key_path: Optional[str] = None


@dataclass(frozen=True)
class VMInfo:
    vm_id: str
    vm_ip: str
    agent_port: int


@dataclass(frozen=True)
class VMLease:
    host: HostInfo
    vm: VMInfo
