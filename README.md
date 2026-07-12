# Proxmox CSI Plugin (Python Implementation)

A minimal Container Storage Interface (CSI) driver for Proxmox Virtual Environment, specifically designed for Shared LVM storage with ext4/xfs filesystems.

## Features

- Dynamic volume provisioning
- Volume expansion (online)
- Raw block volumes
- ext4 and xfs filesystem support
- ReadWriteOnce (SINGLE_NODE_WRITER) access mode
- Split-brain protection for shared storage
- Experimental snapshot support

## Architecture

The driver consists of two components:

- **Controller**: Handles volume lifecycle operations (create, delete, attach, detach, expand)
- **Node**: Handles volume mounting and device operations on each Kubernetes node

## Requirements

- Proxmox VE 7.0 or later
- Kubernetes 1.20 or later
- Shared LVM storage configured in Proxmox

## Releases

Releases are versioned using timestamps in the format `YYYY-MM-DD-HH-MM-SS` (UTC). Each release includes:

- Tagged source code
- Multi-arch Docker images (amd64, arm64) published to GHCR
- Kubernetes manifests with pinned image versions
- Combined `install.yaml` for easy deployment

**Latest Release:** Check [GitHub Releases](https://github.com/adi/proxmox-shared-lvm-csi-plugin/releases)

**Image Versioning:** Images are tagged with timestamp versions (e.g., `2025-12-15-15-28-34`). The `latest` tag is never used to maintain version control.

## Installation

### Quick Install

```bash
# Replace VERSION with the desired release version
VERSION=2025-12-15-15-28-34

# Create secret with Proxmox credentials
kubectl create secret generic proxmox-csi-config \
  --from-literal=config.yaml="$(cat <<EOL
clusters:
  - url: "https://your-proxmox-host:8006/api2/json"
    token_id: "csi@pve!csi-token"
    token_secret: "your-token-secret"
    region: "cluster-1"
    insecure: false
EOL
)" \
  --namespace kube-system

# Install CSI driver
kubectl apply -f https://github.com/adi/proxmox-shared-lvm-csi-plugin/releases/download/${VERSION}/install.yaml
```

### 1. Create Proxmox API Token

Create an API token in Proxmox with the following permissions:

```bash
# In Proxmox, create a user and token:
# Datacenter > Permissions > API Tokens > Add

# Required permissions:
# - Datastore.Allocate
# - Datastore.AllocateSpace
# - VM.Config.Disk
# - VM.Audit
```

### 2. Create Configuration Secret

Edit `deploy/secret.yaml` with your Proxmox credentials:

```yaml
clusters:
  - url: "https://your-proxmox-host:8006/api2/json"
    token_id: "csi@pve!csi-token"
    token_secret: "your-token-secret"
    region: "cluster-1"
    insecure: false  # Set to true to skip TLS verification
```

Alternatively, authenticate with a username/password instead of an API token by
setting `username`/`password` on the cluster entry. This is needed when calling experimental APIs in Proxmox VE (very unfortunate). When both username/password and token_id/token_secret are present username/password take precedence and `token_id`/`token_secret` are ignored:

```yaml
clusters:
  - url: "https://your-proxmox-host:8006/api2/json"
    username: "root@pam"
    password: "your-password"
    region: "cluster-1"
    insecure: false  # Set to true to skip TLS verification
```

Apply the secret:

```bash
kubectl apply -f deploy/secret.yaml
```

### 3. Deploy the CSI Driver

```bash
# Deploy RBAC resources
kubectl apply -f deploy/rbac.yaml

# Deploy controller
kubectl apply -f deploy/controller.yaml

# Deploy node daemonset
kubectl apply -f deploy/node-daemonset.yaml

# Create storage classes
kubectl apply -f deploy/storageclass.yaml
```

### 4. Verify Installation

```bash
# Check controller pod
kubectl get pods -n kube-system -l app=proxmox-csi-controller

# Check node pods
kubectl get pods -n kube-system -l app=proxmox-csi-node

# Check storage classes
kubectl get storageclass
```

## Usage

### Create a PersistentVolumeClaim

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: test-pvc
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: proxmox-lvm-ext4
  resources:
    requests:
      storage: 10Gi
```

### Use in a Pod

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: test-pod
spec:
  containers:
    - name: app
      image: nginx
      volumeMounts:
        - name: data
          mountPath: /data
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: test-pvc
```

### Expand a Volume

Edit the PVC to increase the size:

```bash
kubectl patch pvc test-pvc -p '{"spec":{"resources":{"requests":{"storage":"20Gi"}}}}'
```

The filesystem will be automatically expanded online.

### Raw Block Volume

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: block-pvc
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: proxmox-lvm-block
  volumeMode: Block
  resources:
    requests:
      storage: 10Gi
---
apiVersion: v1
kind: Pod
metadata:
  name: block-pod
spec:
  containers:
    - name: app
      image: busybox
      command: ["sleep", "infinity"]
      volumeDevices:
        - name: data
          devicePath: /dev/xvda
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: block-pvc
```

## Static Volume Provisioning

While the CSI driver supports dynamic provisioning (automatic volume creation), you can also use pre-created volumes with static provisioning.

### Why Static Provisioning?

Use static provisioning when you need to:
- Pre-create volumes with specific LVM properties
- Import existing LVM volumes into Kubernetes
- Have more control over volume creation and placement

### Creating Static Volumes

**Important:** Static volumes must be created using the Proxmox API (not raw `lvcreate`) to ensure they appear in Proxmox UI and are properly registered.

#### Step 1: Create Volume via Proxmox API

From within a controller pod or any environment with access to the Proxmox API:

```bash
# Get a shell in the controller pod
kubectl exec -it -n kube-system deployment/proxmox-csi-controller -c proxmox-csi-controller -- bash

# Create the volume using Python
python3 << 'EOF'
from proxmox_csi.proxmox.client import ProxmoxClient
import yaml

# Load config
with open('/etc/proxmox/config.yaml') as f:
    cfg = yaml.safe_load(f)
c = cfg['clusters'][0]

# Initialize client (token auth shown; pass username=/password= instead for
# ticket-based auth)
client = ProxmoxClient(
    url=c['url'],
    token_id=c.get('token_id'),
    token_secret=c.get('token_secret'),
    insecure=c.get('insecure', False)
)

# Create volume
# IMPORTANT: filename must start with "vm-9999-" prefix
result = client.create_vm_disk(
    vmid=9999,              # Metadata tag (VM 9999 doesn't need to exist)
    node='pve20',           # Your Proxmox node name
    storage='kubedata',     # Your LVM storage name
    filename='vm-9999-myapp-data',  # Must start with vm-9999-
    size_bytes=10 * 1024**3  # 10GB
)
print(f"Volume created: {result}")
EOF
```

**Key Points:**
- The `vmid=9999` is just a metadata tag - VM 9999 does not need to exist
- The `filename` parameter **must** start with `vm-9999-` prefix (Proxmox API requirement)
- The volume will be visible in Proxmox UI under the storage content
- Choose any available Proxmox node for the `node` parameter

#### Step 2: Format the Volume

SSH to a Proxmox node and format the volume:

```bash
# For ext4
mkfs.ext4 /dev/<volume-group>/<volume-name>

# For XFS
mkfs.xfs /dev/<volume-group>/<volume-name>

# Example:
mkfs.xfs /dev/kubedata/vm-9999-myapp-data
```

#### Step 3: Create PersistentVolume in Kubernetes

Create a PV that references the pre-created volume:

```yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: myapp-data-pv
spec:
  capacity:
    storage: 10Gi
  accessModes:
    - ReadWriteOnce
  persistentVolumeReclaimPolicy: Retain
  storageClassName: proxmox-lvm-xfs  # Match your StorageClass
  csi:
    driver: csi.proxmox.sqreept.com
    volumeHandle: /kubedata/vm-9999-myapp-data  # Simplified format: /storage/disk-name
    fsType: xfs
```

**Volume Handle Format:** Use the simplified format `/storage/disk-name`:
- Example: `/kubedata/vm-9999-myapp-data`
- The region and zone are automatically inferred from the driver configuration

#### Step 4: Create PersistentVolumeClaim

Create a PVC that binds to the PV:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: myapp-data
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: proxmox-lvm-xfs
  resources:
    requests:
      storage: 10Gi
  volumeName: myapp-data-pv  # Bind to specific PV
```

#### Step 5: Use in Pod

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: myapp
spec:
  containers:
    - name: app
      image: nginx
      volumeMounts:
        - name: data
          mountPath: /data
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: myapp-data
```

### Static vs Dynamic Provisioning

| Feature | Static Provisioning | Dynamic Provisioning |
|---------|-------------------|---------------------|
| Volume Creation | Manual via Proxmox API | Automatic by CSI driver |
| Control | Full control over LVM properties | Standard properties |
| Naming | Custom (with vm-9999- prefix) | Auto-generated |
| Use Case | Pre-existing volumes, specific requirements | Standard PVC workflow |
| Proxmox UI | Visible (when created via API) | Visible |

## Configuration

### StorageClass Parameters

- `storage`: Proxmox shared LVM storage name (required)
- `cache`: Cache mode for volumes (optional, default: directsync)
  - `none`: No cache
  - `writethrough`: Write-through cache
  - `writeback`: Write-back cache
  - `directsync`: Direct sync (recommended for shared storage)
- `csi.storage.k8s.io/fstype`: Filesystem type (optional, default: ext4)
  - `ext4`: ext4 filesystem
  - `xfs`: xfs filesystem

### Environment Variables

**Controller:**
- `CSI_ENDPOINT`: gRPC endpoint (default: unix:///csi/csi.sock)
- `CLOUD_CONFIG`: Path to Proxmox config file (default: /etc/proxmox/config.yaml)
- `LOG_LEVEL`: Logging level (default: INFO)

**Node:**
- `CSI_ENDPOINT`: gRPC endpoint (default: unix:///csi/csi.sock)
- `NODE_NAME`: Kubernetes node name (required)
- `LOG_LEVEL`: Logging level (default: INFO)

## Release Process (for Maintainers)

Releases are created manually via GitHub Actions:

1. Navigate to **Actions** → **Release** workflow
2. Click **Run workflow**
3. Optionally add a release description
4. Click **Run workflow** button

The workflow will automatically:
- Generate a timestamp-based version tag (e.g., `2025-12-15-15-28-34`)
- Tag the source code and push to GitHub
- Build multi-arch Docker images (amd64, arm64)
- Push images to GHCR with the version tag
- Generate versioned Kubernetes manifests
- Create a GitHub release with all artifacts
- Make manifests available via release URL for direct `kubectl apply`

**Version Format:** `YYYY-MM-DD-HH-MM-SS` (UTC timezone)

**No `latest` tag:** Images are never tagged with `latest` to maintain strict version control.

## Development

### Build Docker Images

```bash
# Build controller image
docker build -f Dockerfile.controller -t proxmox-csi-controller:dev .

# Build node image
docker build -f Dockerfile.node -t proxmox-csi-node:dev .
```

### Run Tests

```bash
# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Run tests (TODO: add tests)
pytest tests/
```

### Project Structure

```
proxmox-shared-lvm-csi-plugin/
├── src/proxmox_csi/
│   ├── main_controller.py       # Controller entrypoint
│   ├── main_node.py             # Node entrypoint
│   ├── grpc_server.py           # gRPC server setup
│   ├── services/
│   │   ├── identity.py          # CSI Identity service
│   │   ├── controller.py        # CSI Controller service
│   │   └── node.py              # CSI Node service
│   ├── proxmox/
│   │   ├── client.py            # Proxmox REST API client
│   │   ├── operations.py        # Volume operations
│   │   └── wwn.py               # WWN/LUN management
│   ├── filesystem/
│   │   ├── format.py            # Filesystem formatting
│   │   ├── mount.py             # Mount operations
│   │   └── resize.py            # Filesystem resize
│   ├── device/
│   │   └── discovery.py         # Device discovery
│   ├── volume/
│   │   └── volume_id.py         # Volume ID handling
│   ├── config.py                # Configuration loading
│   ├── constants.py             # Constants
│   └── utils.py                 # Utilities
├── deploy/
│   ├── rbac.yaml                # RBAC resources
│   ├── controller.yaml          # Controller deployment
│   ├── node-daemonset.yaml      # Node DaemonSet
│   ├── storageclass.yaml        # StorageClass examples
│   └── secret.yaml              # Config secret template
├── requirements.txt             # Python dependencies
├── Dockerfile.controller        # Controller image
├── Dockerfile.node              # Node image
└── README.md                    # This file
```

## Troubleshooting

### Check Controller Logs

```bash
kubectl logs -n kube-system -l app=proxmox-csi-controller -c proxmox-csi-controller
```

### Check Node Logs

```bash
kubectl logs -n kube-system -l app=proxmox-csi-node -c proxmox-csi-node
```

### Common Issues

**Volume attachment fails:**
- Check that the Proxmox API credentials are correct
- Verify that the storage name in StorageClass matches Proxmox
- Check split-brain protection logs (volume may be attached elsewhere)

**Device not found:**
- Wait a few seconds for device discovery (up to 10 seconds)
- Check that SCSI device appears: `ls /sys/bus/scsi/devices/`
- Verify WWN in device attributes: `cat /sys/bus/scsi/devices/*/wwid`

**Mount fails:**
- Check filesystem format: `blkid /dev/sdX`
- Verify mount permissions
- Check node logs for detailed error messages

## Limitations

- Only supports Shared LVM storage (lvm, lvmthin)
- ReadWriteOnce access mode only (no ReadWriteMany)
- No encryption support (LUKS)
- No topology awareness (single cluster)
- Maximum 29 volumes per node (QEMU SCSI limitation, LUN 0 avoided)
- No snapshot or clone support (Proxmox API limitation with LVM storage)

## License

Apache 2.0

## Contributing

Contributions are welcome! Please open an issue or pull request.
