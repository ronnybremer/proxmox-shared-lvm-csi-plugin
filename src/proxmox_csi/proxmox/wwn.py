"""
WWN (World Wide Name) and LUN management for SCSI devices
"""
import hashlib
from typing import Optional, Dict
from ..constants import LUN_MIN, LUN_MAX


def calculate_wwn(disk_name: str) -> str:
    """
    Calculate a stable, unique WWN identifier for a disk (volume).

    The WWN is derived from the disk name only, so it is globally unique per
    volume and independent of which SCSI LUN/slot the disk happens to occupy.
    This avoids collisions when two disks land on the same LUN slot (e.g. from
    concurrent attaches), which previously produced identical WWNs and made
    device discovery on the node ambiguous.

    Format: 16 hex chars (64-bit), leading nibble forced to 5 so it is exposed
    as an NAA-64 IEEE-registered identifier (naa.5...), matching how QEMU/Linux
    surface the ``wwn=0x...`` disk option in ``/sys/block/*/device/wwid``.

    Args:
        disk_name: Disk/volume name (e.g. vm-9999-pvc-abc123)

    Returns:
        WWN hex string (without 0x prefix)

    Example:
        >>> len(calculate_wwn("vm-9999-pvc-abc123"))
        16
        >>> calculate_wwn("vm-9999-pvc-abc123")[0]
        '5'
    """
    digest = hashlib.sha256(disk_name.encode('utf-8')).hexdigest()
    return '5' + digest[1:16]


def extract_wwn(disk_string: str) -> Optional[str]:
    """
    Extract the WWN hex (without 0x prefix) from a Proxmox scsi disk config string.

    Reading the WWN actually recorded in the VM config (rather than recomputing
    it) keeps the idempotent / already-attached paths correct regardless of which
    scheme the disk was attached under - important during a rollout that changes
    how WWNs are derived.

    Args:
        disk_string: e.g. "kubedata:vm-9999-foo,wwn=0x5056...,backup=0"

    Returns:
        WWN hex string without the 0x prefix, or None if not present
    """
    for part in disk_string.split(','):
        part = part.strip()
        if part.startswith('wwn='):
            value = part[len('wwn='):]
            if value.startswith('0x'):
                value = value[2:]
            return value or None
    return None


def find_free_lun(scsi_disks: Dict[str, str], min_lun: int = LUN_MIN,
                 max_lun: int = LUN_MAX) -> Optional[int]:
    """
    Find first available LUN

    Args:
        scsi_disks: Dictionary of existing SCSI disks {device: disk_string}
        min_lun: Minimum LUN number (default: 1)
        max_lun: Maximum LUN number (default: 29)

    Returns:
        First available LUN number, or None if all LUNs are used
    """
    used_luns = set()

    for device in scsi_disks.keys():
        if device.startswith('scsi'):
            try:
                lun_num = int(device[4:])  # Extract number from "scsi5"
                used_luns.add(lun_num)
            except ValueError:
                continue

    for lun in range(min_lun, max_lun + 1):
        if lun not in used_luns:
            return lun

    return None


def is_disk_attached(scsi_disks: Dict[str, str], disk_name: str) -> Optional[int]:
    """
    Check if disk is attached and return LUN

    Args:
        scsi_disks: Dictionary of existing SCSI disks
        disk_name: Disk name to search for

    Returns:
        LUN number if attached, None otherwise
    """
    for device, disk_string in scsi_disks.items():
        if disk_name in disk_string and device.startswith('scsi'):
            try:
                lun = int(device[4:])
                return lun
            except ValueError:
                continue

    return None
