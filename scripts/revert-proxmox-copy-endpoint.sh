#!/bin/bash
#
# Revert the Proxmox VE copy endpoint patch.
#
# Restores the most recent backup of Content.pm created by the patch script.
#
# Usage:
#   sudo bash revert-proxmox-copy-endpoint.sh
#

set -euo pipefail

TARGET="/usr/share/perl5/PVE/API2/Storage/Content.pm"

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root." >&2
    exit 1
fi

if [ ! -f "$TARGET" ]; then
    echo "ERROR: $TARGET not found. Is this a Proxmox VE node?" >&2
    exit 1
fi

# Find the most recent backup
BACKUP=$(ls -t "${TARGET}.bak."* 2>/dev/null | head -1)

if [ -z "$BACKUP" ]; then
    echo "ERROR: No backup file found. Was the patch script ever run?" >&2
    exit 1
fi

echo "Restoring from: $BACKUP"
cp "$BACKUP" "$TARGET"

echo "Restarting pveproxy and pvedaemon..."
systemctl restart pveproxy pvedaemon

echo "Done. Copy endpoint reverted to original state."
