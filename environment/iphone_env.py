from typing import Tuple, Dict, Any, Optional
from .types import VMLease


class IPhoneEnv:
    """
    Gym-like interface for remote iPhone control.
    Manages the lifecycle of a single VM lease and its SSH tunnel.
    """

    def __init__(self, lease: VMLease, max_steps: int = 20):
        """
        Initialize the environment with a pre-acquired lease.
        """
        pass

    @staticmethod
    def create(load_balancer_url: str, max_steps: int = 20) -> "IPhoneEnv":
        """
        Factory method to acquire a lease and instantiate the environment.
        """
        pass

    def _setup_tunnel(self):
        """
        Establishes the SSH bridge between the local machine and the VM.
        """
        pass

    def reset(self) -> np.ndarray:
        """
        Resets simulator state and returns the initial observation.
        """
        pass

    def step(self, action: Any) -> Tuple[np.ndarray, bool, bool, Dict[str, Any]]:
        """
        Sends a command to the VM and returns (obs, terminated, truncated, info).
        """
        pass

    def close(self):
        """
        Teardown the tunnel and release the lease via the Load Balancer.
        """
        pass
