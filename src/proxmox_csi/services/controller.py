"""
CSI Controller Service Implementation
"""
import logging
import threading
import grpc
from typing import Dict
from ..csi_pb2 import (
    CreateVolumeResponse,
    DeleteVolumeResponse,
    ControllerPublishVolumeResponse,
    ControllerUnpublishVolumeResponse,
    ControllerExpandVolumeResponse,
    CreateSnapshotResponse,
    DeleteSnapshotResponse,
    ControllerGetCapabilitiesResponse,
    Volume,
    Snapshot,
    ControllerServiceCapability
)
from ..csi_pb2_grpc import ControllerServicer
from ..proxmox.client import ProxmoxClient
from ..proxmox.operations import (
    create_volume,
    delete_volume,
    attach_volume,
    detach_volume,
    check_existing_attachments,
    create_snapshot,
    clone_volume,
    expand_volume
)
from ..volume.volume_id import parse_volume_id
from ..config import CSIConfig
from ..constants import (
    DRIVER_NAME,
    MIN_VOLUME_SIZE,
    DEFAULT_VOLUME_SIZE
)
from google.protobuf.timestamp_pb2 import Timestamp


logger = logging.getLogger(__name__)


class ControllerService(ControllerServicer):
    """CSI Controller Server"""

    def __init__(self, config: CSIConfig):
        self.config = config
        self.clients: Dict[str, ProxmoxClient] = {}
        self.snapshots_enabled = config.enable_experimental_snapshots

        # Per-VM locks serialize attach/detach so concurrent gRPC threads cannot
        # race on LUN/slot allocation for the same VM. Without this, two
        # ControllerPublishVolume calls to the same node both read the VM config,
        # both pick the same free LUN, and both write scsi<lun> - the second
        # write evicts the first disk from its slot, corrupting the mount.
        self._vm_locks: Dict[int, threading.Lock] = {}
        self._vm_locks_guard = threading.Lock()

        if self.snapshots_enabled:
            logger.warning(
                "EXPERIMENTAL: Snapshot/clone support is enabled. "
                "This requires root@pam authentication and may not work with standard API tokens."
            )

        # Initialize Proxmox clients for each cluster
        for cluster in config.clusters:
            self.clients[cluster.region] = ProxmoxClient(
                url=cluster.url,
                token_id=cluster.token_id,
                token_secret=cluster.token_secret,
                insecure=cluster.insecure
            )

        logger.info(f"Controller service initialized with {len(self.clients)} clusters")

    def _get_default_region(self) -> str:
        """Get default region (first configured region)"""
        if not self.clients:
            return ""
        return next(iter(self.clients.keys()))

    def _vm_lock(self, vmid: int) -> threading.Lock:
        """Return the lock guarding attach/detach for a given VM.

        Serializes the read-config -> find-free-LUN -> write-config critical
        section so concurrent attaches to the same VM cannot pick the same slot.
        """
        with self._vm_locks_guard:
            lock = self._vm_locks.get(vmid)
            if lock is None:
                lock = threading.Lock()
                self._vm_locks[vmid] = lock
            return lock

    def CreateVolume(self, request, context):
        """Create volume"""
        name = request.name
        if not name:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Name must be provided")

        logger.info(f"CreateVolume: {name}")
        logger.debug(f"CreateVolume request: name={name}, parameters={dict(request.parameters or {})}")

        # Get size
        capacity_range = request.capacity_range
        if capacity_range:
            size_bytes = max(capacity_range.required_bytes, MIN_VOLUME_SIZE)
        else:
            size_bytes = DEFAULT_VOLUME_SIZE

        logger.debug(f"CreateVolume: size_bytes={size_bytes}, capacity_range={capacity_range}")

        # Get parameters
        params = request.parameters or {}
        storage = params.get('storage')
        if not storage:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "storage parameter required")

        logger.debug(f"CreateVolume: storage={storage}, all_params={params}")

        # Get region/zone from topology (simplified - use first cluster/node)
        region = list(self.clients.keys())[0]
        client = self.clients[region]
        nodes = client.get_nodes()
        if not nodes:
            context.abort(grpc.StatusCode.INTERNAL, "No nodes available")
        zone = nodes[0]

        # Handle snapshot/clone source
        content_source = request.volume_content_source
        has_content_source = content_source and (
            content_source.HasField('snapshot') or content_source.HasField('volume')
        )

        if has_content_source:
            if not self.snapshots_enabled:
                context.abort(
                    grpc.StatusCode.UNIMPLEMENTED,
                    "Snapshot/clone support is not enabled. "
                    "Set enable_experimental_snapshots: true in config."
                )

            if content_source.HasField('snapshot'):
                source_id = content_source.snapshot.snapshot_id
                logger.info(f"CreateVolume: cloning from snapshot {source_id}")
                volume_id = clone_volume(client, source_id, name, self._get_default_region())
            elif content_source.HasField('volume'):
                source_id = content_source.volume.volume_id
                logger.info(f"CreateVolume: cloning from volume {source_id}")
                volume_id = clone_volume(client, source_id, name, self._get_default_region())
        else:
            # Create new volume
            logger.info(f"CreateVolume: creating new volume on storage={storage}, size={size_bytes}")
            volume_id = create_volume(client, region, zone, storage, name, size_bytes)

        # Return volume
        volume = Volume(
            volume_id=volume_id,
            capacity_bytes=size_bytes
        )

        logger.info(f"Volume created: {volume_id}")
        return CreateVolumeResponse(volume=volume)

    def DeleteVolume(self, request, context):
        """Delete volume"""
        volume_id = request.volume_id
        if not volume_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "VolumeID must be provided")

        logger.info(f"DeleteVolume: {volume_id}")

        try:
            region, zone, storage, disk = parse_volume_id(volume_id, self._get_default_region())
            client = self.clients.get(region)
            if not client:
                context.abort(grpc.StatusCode.NOT_FOUND, f"Region {region} not found")

            delete_volume(client, volume_id, self._get_default_region())

            logger.info(f"Volume deleted: {volume_id}")
            return DeleteVolumeResponse()

        except Exception as e:
            logger.error(f"DeleteVolume failed: {e}", exc_info=True)
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def ControllerPublishVolume(self, request, context):
        """Attach volume to node"""
        volume_id = request.volume_id
        node_id = request.node_id

        if not volume_id or not node_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "VolumeID and NodeID required")

        logger.info(f"ControllerPublishVolume: {volume_id} to {node_id}")

        try:
            region, zone, storage, disk = parse_volume_id(volume_id, self._get_default_region())
            client = self.clients.get(region)
            if not client:
                context.abort(grpc.StatusCode.NOT_FOUND, f"Region {region} not found")

            # Discover VMID from node_id (Kubernetes node name)
            logger.debug(f"ControllerPublishVolume: discovering VM ID for node {node_id}")

            # Try to parse as integer first (for explicit VMID)
            try:
                vmid = int(node_id)
                logger.info(f"ControllerPublishVolume: using explicit VMID {vmid}")
            except ValueError:
                # Node name provided, discover VM from Proxmox
                vm_info = client.find_vm_by_name(node_id)
                if vm_info is None:
                    context.abort(
                        grpc.StatusCode.NOT_FOUND,
                        f"No VM found with name '{node_id}' in Proxmox cluster"
                    )
                vmid, vm_node = vm_info
                logger.info(f"ControllerPublishVolume: discovered VM {vmid} on node {vm_node} for Kubernetes node {node_id}")

            # Serialize the check + attach for this VM. Concurrent attaches to
            # the same VM must not both read the config, pick the same free LUN,
            # and write the same scsi<lun> slot (the second write evicts the
            # first disk and corrupts its mount).
            with self._vm_lock(vmid):
                # CRITICAL: Split-brain protection
                existing_vmid, _ = check_existing_attachments(client, region, storage, disk)

                if existing_vmid is not None and existing_vmid != vmid:
                    # Attached to a different VM - SPLIT-BRAIN PROTECTION
                    context.abort(
                        grpc.StatusCode.FAILED_PRECONDITION,
                        f"Volume {volume_id} already attached to VM {existing_vmid}"
                    )

                # Attach volume. attach_volume is idempotent: if the disk is
                # already on this VM it returns the existing device path (with
                # the WWN read from the VM config) instead of re-attaching.
                publish_context = attach_volume(client, vmid, volume_id, self._get_default_region())

            logger.info(f"Volume {volume_id} attached to VM {vmid}")
            return ControllerPublishVolumeResponse(publish_context=publish_context)

        except Exception as e:
            logger.error(f"ControllerPublishVolume failed: {e}", exc_info=True)
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def ControllerUnpublishVolume(self, request, context):
        """Detach volume from node"""
        volume_id = request.volume_id
        node_id = request.node_id

        if not volume_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "VolumeID required")

        logger.info(f"ControllerUnpublishVolume: {volume_id} from {node_id}")

        try:
            region, zone, storage, disk = parse_volume_id(volume_id, self._get_default_region())
            client = self.clients.get(region)
            if not client:
                context.abort(grpc.StatusCode.NOT_FOUND, f"Region {region} not found")

            # Discover VMID from node_id (Kubernetes node name)
            if not node_id:
                # If node_id not provided, search for which VM has the volume attached
                logger.warning(f"ControllerUnpublishVolume: no node_id provided, searching for attachment")
                existing_vmid, _ = check_existing_attachments(client, region, storage, disk)
                if existing_vmid is None:
                    logger.info(f"ControllerUnpublishVolume: volume {volume_id} not attached anywhere")
                    return ControllerUnpublishVolumeResponse()
                vmid = existing_vmid
            else:
                logger.debug(f"ControllerUnpublishVolume: discovering VM ID for node {node_id}")
                try:
                    vmid = int(node_id)
                    logger.info(f"ControllerUnpublishVolume: using explicit VMID {vmid}")
                except ValueError:
                    vm_info = client.find_vm_by_name(node_id)
                    if vm_info is None:
                        # For unpublish, if VM not found, it's likely already deleted
                        # This is idempotent, so just return success
                        logger.warning(f"ControllerUnpublishVolume: VM '{node_id}' not found, assuming already detached")
                        return ControllerUnpublishVolumeResponse()
                    vmid, vm_node = vm_info
                    logger.info(f"ControllerUnpublishVolume: discovered VM {vmid} on node {vm_node}")

            # Detach volume (serialized against attach/detach on the same VM)
            with self._vm_lock(vmid):
                detach_volume(client, vmid, volume_id, self._get_default_region())

            logger.info(f"Volume {volume_id} detached from VM {vmid}")
            return ControllerUnpublishVolumeResponse()

        except Exception as e:
            logger.error(f"ControllerUnpublishVolume failed: {e}", exc_info=True)
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def ControllerExpandVolume(self, request, context):
        """Expand volume"""
        volume_id = request.volume_id
        if not volume_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "VolumeID required")

        capacity_range = request.capacity_range
        if not capacity_range:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "CapacityRange required")

        new_size = capacity_range.required_bytes

        logger.info(f"ControllerExpandVolume: {volume_id} to {new_size} bytes")

        try:
            region, zone, storage, disk = parse_volume_id(volume_id, self._get_default_region())
            client = self.clients.get(region)
            if not client:
                context.abort(grpc.StatusCode.NOT_FOUND, f"Region {region} not found")

            # For expansion, volume must be attached. Find which VM it's attached to.
            logger.debug(f"ControllerExpandVolume: finding which VM has volume {volume_id} attached")
            existing_vmid, _ = check_existing_attachments(client, region, storage, disk)
            if existing_vmid is None:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    f"Volume {volume_id} must be attached to a VM to expand"
                )

            logger.info(f"ControllerExpandVolume: volume attached to VM {existing_vmid}")
            expand_volume(client, existing_vmid, volume_id, new_size, self._get_default_region())

            logger.info(f"Volume {volume_id} expanded to {new_size} bytes")
            return ControllerExpandVolumeResponse(
                capacity_bytes=new_size,
                node_expansion_required=True
            )

        except Exception as e:
            logger.error(f"ControllerExpandVolume failed: {e}", exc_info=True)
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def CreateSnapshot(self, request, context):
        """Create snapshot (EXPERIMENTAL)"""
        if not self.snapshots_enabled:
            context.abort(
                grpc.StatusCode.UNIMPLEMENTED,
                "Snapshot support is not enabled. Set enable_experimental_snapshots: true in config."
            )

        source_volume_id = request.source_volume_id
        name = request.name

        if not source_volume_id or not name:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "SourceVolumeID and Name required")

        logger.info(f"CreateSnapshot: {name} from {source_volume_id}")

        try:
            region, zone, storage, disk = parse_volume_id(source_volume_id, self._get_default_region())
            client = self.clients.get(region)
            if not client:
                context.abort(grpc.StatusCode.NOT_FOUND, f"Region {region} not found")

            snapshot_id = create_snapshot(client, source_volume_id, name, self._get_default_region())

            snapshot = Snapshot(
                snapshot_id=snapshot_id,
                source_volume_id=source_volume_id,
                creation_time=Timestamp(),
                ready_to_use=True
            )

            logger.info(f"Snapshot created: {snapshot_id}")
            return CreateSnapshotResponse(snapshot=snapshot)

        except Exception as e:
            logger.error(f"CreateSnapshot failed: {e}", exc_info=True)
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def DeleteSnapshot(self, request, context):
        """Delete snapshot (EXPERIMENTAL)"""
        if not self.snapshots_enabled:
            context.abort(
                grpc.StatusCode.UNIMPLEMENTED,
                "Snapshot support is not enabled. Set enable_experimental_snapshots: true in config."
            )

        snapshot_id = request.snapshot_id
        if not snapshot_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "SnapshotID required")

        logger.info(f"DeleteSnapshot: {snapshot_id}")

        try:
            region, zone, storage, disk = parse_volume_id(snapshot_id, self._get_default_region())
            client = self.clients.get(region)
            if not client:
                context.abort(grpc.StatusCode.NOT_FOUND, f"Region {region} not found")

            delete_volume(client, snapshot_id, self._get_default_region())

            logger.info(f"Snapshot deleted: {snapshot_id}")
            return DeleteSnapshotResponse()

        except Exception as e:
            logger.error(f"DeleteSnapshot failed: {e}", exc_info=True)
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def ControllerGetCapabilities(self, request, context):
        """Return controller capabilities"""
        logger.debug("ControllerGetCapabilities called")

        capabilities = [
            ControllerServiceCapability(
                rpc=ControllerServiceCapability.RPC(
                    type=ControllerServiceCapability.RPC.CREATE_DELETE_VOLUME
                )
            ),
            ControllerServiceCapability(
                rpc=ControllerServiceCapability.RPC(
                    type=ControllerServiceCapability.RPC.PUBLISH_UNPUBLISH_VOLUME
                )
            ),
            ControllerServiceCapability(
                rpc=ControllerServiceCapability.RPC(
                    type=ControllerServiceCapability.RPC.EXPAND_VOLUME
                )
            ),
        ]

        if self.snapshots_enabled:
            capabilities.append(
                ControllerServiceCapability(
                    rpc=ControllerServiceCapability.RPC(
                        type=ControllerServiceCapability.RPC.CREATE_DELETE_SNAPSHOT
                    )
                )
            )
            capabilities.append(
                ControllerServiceCapability(
                    rpc=ControllerServiceCapability.RPC(
                        type=ControllerServiceCapability.RPC.CLONE_VOLUME
                    )
                )
            )

        return ControllerGetCapabilitiesResponse(capabilities=capabilities)
