#!/bin/bash
#
# Patch Proxmox VE to allow API token authentication for volume copy operations.
#
# The copy endpoint in PVE::API2::Storage::Content has no 'permissions' block
# and no 'allowtoken' flag, so only root@pam can use it. This patch adds both,
# requiring Datastore.Allocate permission on the storage.
#
# WARNING:
#   - This modifies a Proxmox system file. Package updates will overwrite it.
#   - The copy endpoint is marked as experimental by Proxmox upstream.
#   - Test in a non-production environment first.
#
# Usage:
#   sudo bash patch-proxmox-copy-endpoint.sh
#
# To revert:
#   sudo cp /usr/share/perl5/PVE/API2/Storage/Content.pm.bak \
#           /usr/share/perl5/PVE/API2/Storage/Content.pm
#   sudo systemctl restart pveproxy pvedaemon
#

set -euo pipefail

TARGET="/usr/share/perl5/PVE/API2/Storage/Content.pm"

# --- Preflight checks ---

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root." >&2
    exit 1
fi

if [ ! -f "$TARGET" ]; then
    echo "ERROR: $TARGET not found. Is this a Proxmox VE node?" >&2
    exit 1
fi

# Check if already patched
if grep -q "allowtoken => 1" "$TARGET" 2>/dev/null; then
    echo "Already patched. Nothing to do."
    exit 0
fi

# Verify the expected code is present (sanity check)
if ! grep -q "name => 'copy'" "$TARGET"; then
    echo "ERROR: Could not find the copy endpoint in $TARGET." >&2
    echo "       The file format may have changed. Aborting." >&2
    exit 1
fi

# --- Backup ---

BACKUP="${TARGET}.bak.$(date +%Y%m%d%H%M%S)"
cp "$TARGET" "$BACKUP"
echo "Backup saved to: $BACKUP"

# --- Apply patch ---

# Add 'allowtoken => 1' and a permissions block after 'protected => 1' in the copy endpoint.
#
# We match the specific pattern unique to the copy endpoint:
#   description => "Copy a volume. This is experimental code - do not use.",
#   protected => 1,
#   proxyto => 'node',
#
# and replace it with the patched version.

sed -i '
/description => "Copy a volume\. This is experimental code - do not use\.",/{
    N
    N
    s|description => "Copy a volume. This is experimental code - do not use.",\n\tprotected => 1,\n\tproxyto => '\''node'\''|description => "Copy a volume.",\n\tprotected => 1,\n\tallowtoken => 1,\n\tproxyto => '\''node'\'',\n\tpermissions => {\n\t    check => ['\''perm'\'', '\''/storage/{storage}'\'', ['\''Datastore.Allocate'\'']],\n\t}|
}
' "$TARGET"

# --- Verify ---

if grep -q "allowtoken => 1" "$TARGET"; then
    echo "Patch applied successfully."
else
    echo "ERROR: Patch verification failed. Restoring backup." >&2
    cp "$BACKUP" "$TARGET"
    exit 1
fi

# --- Restart services ---

echo "Restarting pveproxy and pvedaemon..."
systemctl restart pveproxy pvedaemon

echo ""
echo "Done. The copy endpoint now accepts API tokens with Datastore.Allocate permission."
echo "To revert: cp $BACKUP $TARGET && systemctl restart pveproxy pvedaemon"
