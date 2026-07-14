"""
Proxmox volume operations with split-brain protection
"""
import logging
from typing import Dict, Optional, Tuple
from .client import ProxmoxClient
from .wwn import calculate_wwn, extract_wwn, find_free_lun, is_disk_attached
from ..constants import STORAGE_VMID, DEVICE_PREFIX
from ..volume.volume_id import parse_volume_id, create_volume_id, build_disk_name


logger = logging.getLogger(__name__)


class UnsupportedVolumeFormatError(Exception):
    """
    Raised when CreateSnapshot is attempted on a volume whose format does not
    support snapshots. Per Proxmox documentation, snapshots require qcow2;
    this is a permanent condition for the volume as-is and must not be retried
    by the caller without recreating the volume in qcow2 format.
    """


def create_volume(client: ProxmoxClient, region: str, zone: str,
                 storage: str, pvc_name: str, size_bytes: int,
                 volume_format: str) -> str:
    """
    Create LVM volume on Proxmox

    Args:
        client: Proxmox API client
        region: Cluster region
        zone: Node name
        storage: Storage ID
        pvc_name: PVC name
        size_bytes: Volume size in bytes
        volume_format: Disk format to request ('raw' or 'qcow2'); always sent
            to Proxmox explicitly so behavior never depends on the storage's
            own default. 'qcow2' is required for snapshot support.

    Returns:
        Volume ID string
    """
    disk_name = build_disk_name(pvc_name, STORAGE_VMID, volume_format)
    size_gib = size_bytes / (1024 ** 3)

    logger.info(f"Creating volume {disk_name} on {zone}/{storage}, size={size_bytes} bytes "
               f"({size_gib:.2f} GiB), format={volume_format}")
    logger.debug(f"create_volume params: region={region}, zone={zone}, storage={storage}, "
                f"pvc_name={pvc_name}, STORAGE_VMID={STORAGE_VMID}, disk_name={disk_name}, "
                f"volume_format={volume_format}")

    client.create_vm_disk(
        vmid=STORAGE_VMID,
        node=zone,
        storage=storage,
        filename=disk_name,
        size_bytes=size_bytes,
        format=volume_format
    )

    volume_id = create_volume_id(region, zone, storage, pvc_name, STORAGE_VMID, volume_format)

    logger.info(f"Volume created: {volume_id}")
    return volume_id


def delete_volume(client: ProxmoxClient, volume_id: str, default_region: str = "") -> bool:
    """
    Delete volume

    Args:
        client: Proxmox API client
        volume_id: Volume ID
        default_region: Default region for parsing volume_id

    Returns:
        True if successful
    """
    region, zone, storage, disk = parse_volume_id(volume_id, default_region)

    logger.info(f"Deleting volume {volume_id}")

    client.delete_vm_disk(
        vmid=STORAGE_VMID,
        node=zone,
        storage=storage,
        volume=disk
    )

    logger.info(f"Volume deleted: {volume_id}")
    return True


def attach_volume(client: ProxmoxClient, vmid: int, volume_id: str, default_region: str = "") -> Dict[str, str]:
    """
    Attach volume to VM with WWN identifier

    Args:
        client: Proxmox API client
        vmid: VM ID to attach to
        volume_id: Volume ID
        default_region: Default region for parsing volume_id

    Returns:
        Publish context with DevicePath and lun

    Raises:
        Exception: If no free LUN or attachment fails
    """
    region, zone, storage, disk = parse_volume_id(volume_id, default_region)

    logger.info(f"Attaching volume {volume_id} to VM {vmid}")

    # Find which node the VM is currently on
    vm_node = client.find_vm_node(vmid)
    if vm_node is None:
        raise Exception(f"VM {vmid} not found on any node")

    logger.debug(f"VM {vmid} is on node {vm_node}")

    # Get VM config
    vm_config = client.get_vm_config(vmid, vm_node)
    scsi_disks = client.extract_scsi_disks(vm_config)

    # Check if already attached
    existing_lun = is_disk_attached(scsi_disks, disk)
    if existing_lun is not None:
        logger.info(f"Volume {volume_id} already attached to VM {vmid} at LUN {existing_lun}")
        # Report the WWN actually recorded in the VM config so the device path
        # matches reality even for disks attached under an older WWN scheme.
        wwn = extract_wwn(scsi_disks.get(f"{DEVICE_PREFIX}{existing_lun}", '')) or calculate_wwn(disk)
        return {
            'DevicePath': f'/dev/disk/by-id/wwn-0x{wwn}',
            'lun': str(existing_lun)
        }

    # Find free LUN
    lun = find_free_lun(scsi_disks)
    if lun is None:
        raise Exception(f"No free LUN available for VM {vmid}")

    # Calculate WWN (derived from the disk name - unique and stable per volume)
    wwn = calculate_wwn(disk)

    # Attach disk
    device = f"{DEVICE_PREFIX}{lun}"
    disk_string = f"{storage}:{disk},wwn=0x{wwn},backup=0"

    logger.info(f"Attaching {disk} to VM {vmid} on node {vm_node} as {device} with WWN 0x{wwn}")

    client.update_vm_config(vmid, vm_node, {device: disk_string})

    return {
        'DevicePath': f'/dev/disk/by-id/wwn-0x{wwn}',
        'lun': str(lun)
    }


def detach_volume(client: ProxmoxClient, vmid: int, volume_id: str, default_region: str = "") -> bool:
    """
    Detach volume from VM

    Args:
        client: Proxmox API client
        vmid: VM ID
        volume_id: Volume ID
        default_region: Default region for parsing volume_id

    Returns:
        True if successful
    """
    region, zone, storage, disk = parse_volume_id(volume_id, default_region)

    logger.info(f"Detaching volume {volume_id} from VM {vmid}")

    # Find which node the VM is currently on (it might have migrated)
    vm_node = client.find_vm_node(vmid)
    if vm_node is None:
        logger.warning(f"VM {vmid} not found on any node, assuming already deleted")
        return True

    logger.debug(f"VM {vmid} is on node {vm_node}")

    # Get VM config
    vm_config = client.get_vm_config(vmid, vm_node)
    scsi_disks = client.extract_scsi_disks(vm_config)

    # Find disk
    lun = is_disk_attached(scsi_disks, disk)
    if lun is None:
        logger.warning(f"Volume {volume_id} not attached to VM {vmid}, already detached")
        return True

    # Detach disk
    device = f"{DEVICE_PREFIX}{lun}"

    logger.info(f"Detaching device {device} from VM {vmid} on node {vm_node}")

    # Proxmox API expects 'delete' parameter with device name
    client.update_vm_config(vmid, vm_node, {
        'delete': device
    })

    logger.info(f"Volume {volume_id} detached from VM {vmid}")
    return True


def check_existing_attachments(client: ProxmoxClient, region: str,
                               storage: str, disk_name: str) -> Tuple[Optional[int], Optional[int]]:
    """
    CRITICAL: Split-brain protection

    Scan all VMs across all nodes to find existing attachments of a disk.
    This prevents double-attachment which could cause data corruption.

    Args:
        client: Proxmox API client
        region: Cluster region
        storage: Storage ID
        disk_name: Disk name to search for

    Returns:
        Tuple of (vmid, lun) if found, (None, None) otherwise
    """
    logger.info(f"Checking for existing attachments of {disk_name}")

    nodes = client.get_nodes()

    for node in nodes:
        try:
            vms = client.get_vms(node)

            for vm in vms:
                vmid = vm['vmid']

                # Skip STORAGE_VMID (volumes at rest, not attached to workers)
                if vmid == STORAGE_VMID:
                    continue

                try:
                    vm_config = client.get_vm_config(vmid, node)
                    scsi_disks = client.extract_scsi_disks(vm_config)

                    lun = is_disk_attached(scsi_disks, disk_name)
                    if lun is not None:
                        logger.warning(f"Volume {disk_name} is already attached to VM {vmid} (LUN {lun}) on node {node}")
                        return vmid, lun

                except Exception as e:
                    logger.error(f"Failed to check VM {vmid} on {node}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Failed to get VMs from node {node}: {e}")
            continue

    logger.info(f"No existing attachments found for {disk_name}")
    return None, None


def create_snapshot(client: ProxmoxClient, source_volume_id: str,
                    snapshot_name: str, default_region: str = "") -> str:
    """
    Create snapshot via Proxmox copy operation

    EXPERIMENTAL: Requires root@pam authentication.

    Args:
        client: Proxmox API client
        source_volume_id: Source volume ID
        snapshot_name: Snapshot name
        default_region: Default region for parsing volume_id

    Returns:
        Snapshot volume ID

    Raises:
        UnsupportedVolumeFormatError: If the source volume's format is not qcow2
    """
    region, zone, storage, source_disk = parse_volume_id(source_volume_id, default_region)

    volume_format = client.get_volume_format(zone, storage, source_disk)
    if volume_format != 'qcow2':
        raise UnsupportedVolumeFormatError(
            f"Volume {source_volume_id} has format '{volume_format or 'unknown'}'; "
            "Proxmox only supports snapshots on qcow2 volumes. Recreate the volume "
            "with format=qcow2 to enable snapshot support."
        )

    snapshot_disk = build_disk_name(snapshot_name, STORAGE_VMID, 'qcow2')

    logger.info(f"Creating snapshot {snapshot_disk} from {source_disk}")

    client.copy_volume(
        node=zone,
        storage=storage,
        volume=source_disk,
        target_name=snapshot_disk
    )

    snapshot_id = create_volume_id(region, zone, storage, snapshot_name, STORAGE_VMID, 'qcow2')

    logger.info(f"Snapshot created: {snapshot_id}")
    return snapshot_id


def clone_volume(client: ProxmoxClient, source_volume_id: str,
                 target_pvc_name: str, default_region: str = "") -> str:
    """
    Clone volume from snapshot or volume

    EXPERIMENTAL: Requires root@pam authentication.

    Args:
        client: Proxmox API client
        source_volume_id: Source volume ID
        target_pvc_name: Target PVC name
        default_region: Default region for parsing volume_id

    Returns:
        Target volume ID
    """
    src_region, src_zone, src_storage, src_disk = parse_volume_id(source_volume_id, default_region)

    # Clone must be created in the same format as its source; the source disk
    # name itself carries a '.qcow2' suffix when it was created in that format.
    volume_format = 'qcow2' if src_disk.endswith('.qcow2') else None
    target_disk = build_disk_name(target_pvc_name, STORAGE_VMID, volume_format)

    logger.info(f"Cloning {src_disk} to {target_disk}")

    client.copy_volume(
        node=src_zone,
        storage=src_storage,
        volume=src_disk,
        target_name=target_disk
    )

    target_id = create_volume_id(src_region, src_zone, src_storage, target_pvc_name, STORAGE_VMID, volume_format)

    logger.info(f"Volume cloned: {target_id}")
    return target_id


def expand_volume(client: ProxmoxClient, vmid: int, volume_id: str,
                 new_size_bytes: int, default_region: str = "") -> bool:
    """
    Expand volume at storage level

    Args:
        client: Proxmox API client
        vmid: VM ID (volume must be attached)
        volume_id: Volume ID
        new_size_bytes: New size in bytes
        default_region: Default region for parsing volume_id

    Returns:
        True if successful

    Raises:
        Exception: If volume not attached or resize fails
    """
    region, zone, storage, disk = parse_volume_id(volume_id, default_region)

    logger.info(f"Expanding volume {volume_id} to {new_size_bytes} bytes")

    # Find which node the VM is currently on
    vm_node = client.find_vm_node(vmid)
    if vm_node is None:
        raise Exception(f"VM {vmid} not found on any node")

    logger.debug(f"VM {vmid} is on node {vm_node}")

    # Get VM config to find LUN
    vm_config = client.get_vm_config(vmid, vm_node)
    scsi_disks = client.extract_scsi_disks(vm_config)

    lun = is_disk_attached(scsi_disks, disk)
    if lun is None:
        raise Exception(f"Volume {volume_id} not attached to VM {vmid}, cannot resize")

    device = f"{DEVICE_PREFIX}{lun}"
    size_mb = new_size_bytes // (1024 * 1024)

    logger.info(f"Resizing device {device} to {size_mb}M on node {vm_node}")

    client.resize_vm_disk(vmid, vm_node, device, f"{size_mb}M")

    logger.info(f"Volume {volume_id} expanded successfully")
    return True
