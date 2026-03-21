"""
Constants for Proxmox CSI Driver
"""

# Driver information
DRIVER_NAME = "csi.proxmox.sqreept.com"
DRIVER_VERSION = "0.1.0"

# Proxmox VM ID used as metadata tag for CSI volumes
# This is just a tag - VM 9999 does not need to exist
# Helps identify CSI-managed volumes in Proxmox UI
STORAGE_VMID = 9999

# SCSI LUN limits (QEMU has max 30 SCSI devices, avoid LUN 0)
LUN_MIN = 1
LUN_MAX = 29

# Device naming
DEVICE_PREFIX = "scsi"

# Volume size constraints (in bytes)
MIN_VOLUME_SIZE = 512 * 1024 * 1024  # 512 MiB
DEFAULT_VOLUME_SIZE = 10 * 1024 * 1024 * 1024  # 10 GiB

# Filesystem types
FS_TYPE_EXT4 = "ext4"
FS_TYPE_XFS = "xfs"
DEFAULT_FS_TYPE = FS_TYPE_EXT4

# Device discovery
SCSI_DEVICES_PATH = "/sys/bus/scsi/devices"
DEVICE_DISCOVERY_TIMEOUT = 10  # seconds
DEVICE_DISCOVERY_INTERVAL = 0.05  # 50ms

# Volume capabilities
MAX_VOLUMES_PER_NODE = 29  # Matches LUN range 1-29 (QEMU 30 SCSI limit, LUN 0 avoided)

# CSI capabilities
CONTROLLER_CAPABILITIES = [
    "CREATE_DELETE_VOLUME",
    "PUBLISH_UNPUBLISH_VOLUME",
    "EXPAND_VOLUME",
]

# Experimental capabilities (require enable_experimental_snapshots and root@pam auth)
EXPERIMENTAL_CONTROLLER_CAPABILITIES = [
    "CREATE_DELETE_SNAPSHOT",
    "CLONE_VOLUME",
]

NODE_CAPABILITIES = [
    "STAGE_UNSTAGE_VOLUME",
    "EXPAND_VOLUME",
    "GET_VOLUME_STATS",
]

PLUGIN_CAPABILITIES = [
    "CONTROLLER_SERVICE",
    "VOLUME_ACCESSIBILITY_CONSTRAINTS",
]

# Access modes
ACCESS_MODE_SINGLE_NODE_WRITER = "SINGLE_NODE_WRITER"

# Volume ID format: region/zone/storage/disk-name
VOLUME_ID_SEPARATOR = "/"
VOLUME_ID_PARTS = 4
