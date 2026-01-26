"""Unit tests for HostController.

These tests assume the local machine acts as the host.
No actual VMs are required - we test the pool management logic.
"""

from __future__ import annotations

import concurrent.futures
import threading
import pytest

from environment.host_controller import HostController, VMInfo


def make_vms(count: int) -> list[VMInfo]:
    """Create a list of mock VMInfo objects."""
    return [
        VMInfo(vm_id=f"vm-{i}", vm_ip=f"192.168.64.{i + 2}", agent_port=8000)
        for i in range(count)
    ]


class TestVMInfo:
    def test_vminfo_is_frozen(self) -> None:
        vm = VMInfo(vm_id="vm-1", vm_ip="192.168.64.2", agent_port=8000)
        with pytest.raises(AttributeError):
            vm.vm_id = "vm-2"  # type: ignore

    def test_vminfo_equality(self) -> None:
        vm1 = VMInfo(vm_id="vm-1", vm_ip="192.168.64.2", agent_port=8000)
        vm2 = VMInfo(vm_id="vm-1", vm_ip="192.168.64.2", agent_port=8000)
        assert vm1 == vm2


class TestHostControllerBasic:
    def test_acquire_single_vm(self) -> None:
        vms = make_vms(1)
        controller = HostController(vms)

        vm = controller.acquire_vm()

        assert vm.vm_id == "vm-0"
        assert vm.vm_ip == "192.168.64.2"
        assert vm.agent_port == 8000
        assert controller.get_free_count() == 0
        assert controller.get_leased_count() == 1

    def test_acquire_multiple_vms(self) -> None:
        vms = make_vms(3)
        controller = HostController(vms)

        vm1 = controller.acquire_vm()
        vm2 = controller.acquire_vm()

        assert vm1.vm_id != vm2.vm_id
        assert controller.get_free_count() == 1
        assert controller.get_leased_count() == 2

    def test_acquire_no_vms_available(self) -> None:
        vms = make_vms(1)
        controller = HostController(vms)
        controller.acquire_vm()

        with pytest.raises(RuntimeError, match="No VMs available"):
            controller.acquire_vm(timeout=0)

    def test_release_vm(self) -> None:
        vms = make_vms(1)
        controller = HostController(vms)
        vm = controller.acquire_vm()

        result = controller.release_vm(vm.vm_id)

        assert result is True
        assert controller.get_free_count() == 1
        assert controller.get_leased_count() == 0

    def test_release_vm_not_leased(self) -> None:
        vms = make_vms(1)
        controller = HostController(vms)

        with pytest.raises(ValueError, match="not currently leased"):
            controller.release_vm("vm-0")

    def test_release_unknown_vm(self) -> None:
        vms = make_vms(1)
        controller = HostController(vms)

        with pytest.raises(ValueError, match="not currently leased"):
            controller.release_vm("unknown-vm")

    def test_acquire_after_release(self) -> None:
        vms = make_vms(1)
        controller = HostController(vms)
        vm = controller.acquire_vm()
        controller.release_vm(vm.vm_id)

        vm2 = controller.acquire_vm()

        assert vm2.vm_id == vm.vm_id
        assert controller.get_leased_count() == 1

    def test_list_vms(self) -> None:
        vms = make_vms(3)
        controller = HostController(vms)

        all_vms = controller.list_vms()

        assert len(all_vms) == 3
        assert "vm-0" in all_vms
        assert "vm-1" in all_vms
        assert "vm-2" in all_vms

    def test_list_vms_returns_copy(self) -> None:
        vms = make_vms(1)
        controller = HostController(vms)

        all_vms = controller.list_vms()
        all_vms["vm-999"] = VMInfo("vm-999", "1.2.3.4", 9999)

        assert "vm-999" not in controller.list_vms()


class TestHostControllerMaxConcurrent:
    def test_max_concurrent_enforced(self) -> None:
        vms = make_vms(5)
        controller = HostController(vms, max_concurrent=2)

        controller.acquire_vm()
        controller.acquire_vm()

        with pytest.raises(RuntimeError, match="Max concurrent leases reached"):
            controller.acquire_vm(timeout=0)

    def test_max_concurrent_after_release(self) -> None:
        vms = make_vms(5)
        controller = HostController(vms, max_concurrent=2)

        vm1 = controller.acquire_vm()
        controller.acquire_vm()
        controller.release_vm(vm1.vm_id)

        vm3 = controller.acquire_vm()
        assert vm3 is not None


class TestHostControllerThreadSafety:
    def test_concurrent_acquire(self) -> None:
        vms = make_vms(10)
        controller = HostController(vms)
        acquired_vms: list[VMInfo] = []
        lock = threading.Lock()

        def acquire() -> None:
            vm = controller.acquire_vm()
            with lock:
                acquired_vms.append(vm)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(acquire) for _ in range(10)]
            concurrent.futures.wait(futures)

        assert len(acquired_vms) == 10
        vm_ids = {vm.vm_id for vm in acquired_vms}
        assert len(vm_ids) == 10  # All unique

    def test_concurrent_acquire_exhausts_pool(self) -> None:
        vms = make_vms(5)
        controller = HostController(vms)
        errors: list[Exception] = []
        acquired: list[VMInfo] = []
        lock = threading.Lock()

        def try_acquire() -> None:
            try:
                vm = controller.acquire_vm(timeout=0)
                with lock:
                    acquired.append(vm)
            except RuntimeError as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(try_acquire) for _ in range(10)]
            concurrent.futures.wait(futures)

        assert len(acquired) == 5
        assert len(errors) == 5
        for e in errors:
            assert "No VMs available" in str(e)

    def test_concurrent_acquire_release(self) -> None:
        vms = make_vms(3)
        controller = HostController(vms)

        def acquire_and_release() -> None:
            vm = controller.acquire_vm()
            controller.release_vm(vm.vm_id)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(acquire_and_release) for _ in range(100)]
            concurrent.futures.wait(futures)

        assert controller.get_free_count() == 3
        assert controller.get_leased_count() == 0


class TestHostControllerEmpty:
    def test_empty_pool(self) -> None:
        controller = HostController([])

        with pytest.raises(RuntimeError, match="No VMs available"):
            controller.acquire_vm(timeout=0)

    def test_empty_pool_list_vms(self) -> None:
        controller = HostController([])

        assert controller.list_vms() == {}
        assert controller.get_free_count() == 0
        assert controller.get_leased_count() == 0
