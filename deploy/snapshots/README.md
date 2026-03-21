# Experimental: Snapshots and Clones

Snapshot and clone support requires the Proxmox API `copy_volume` operation, which has a hardcoded check requiring `root@pam` user for LVM storage. This means you must create an API token under the `root@pam` user.

## Prerequisites

1. **API token under `root@pam`**: Create a token for `root@pam` in Proxmox (Datacenter > Permissions > API Tokens). Use this token in your CSI config secret.

2. **CSI Snapshot CRDs**: Install the Kubernetes snapshot CRDs:
   ```bash
   kubectl apply -f https://raw.githubusercontent.com/kubernetes-csi/external-snapshotter/v8.2.0/client/config/crd/snapshot.storage.k8s.io_volumesnapshotclasses.yaml
   kubectl apply -f https://raw.githubusercontent.com/kubernetes-csi/external-snapshotter/v8.2.0/client/config/crd/snapshot.storage.k8s.io_volumesnapshotcontents.yaml
   kubectl apply -f https://raw.githubusercontent.com/kubernetes-csi/external-snapshotter/v8.2.0/client/config/crd/snapshot.storage.k8s.io_volumesnapshots.yaml
   ```

## Setup

1. **Enable the feature flag** in your config secret:
   ```yaml
   enable_experimental_snapshots: true
   clusters:
     - url: "https://proxmox.example.com:8006/api2/json"
       token_id: "root@pam!csi-token"
       token_secret: "your-token-secret"
       region: "cluster-1"
   ```

2. **Apply snapshot RBAC**:
   ```bash
   kubectl apply -f deploy/snapshots/rbac-patch.yaml
   ```

3. **Add the snapshotter sidecar** to the controller:
   ```bash
   kubectl patch deployment proxmox-csi-controller -n kube-system \
     --type=strategic --patch-file deploy/snapshots/controller-patch.yaml
   ```

4. **Create the VolumeSnapshotClass**:
   ```bash
   kubectl apply -f deploy/snapshots/volumesnapshotclass.yaml
   ```

## Usage

### Create a Snapshot

```yaml
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: my-snapshot
spec:
  volumeSnapshotClassName: proxmox-snapshot
  source:
    persistentVolumeClaimName: my-pvc
```

### Restore from Snapshot

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: restored-pvc
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: proxmox-lvm-ext4
  resources:
    requests:
      storage: 10Gi
  dataSource:
    name: my-snapshot
    kind: VolumeSnapshot
    apiGroup: snapshot.storage.k8s.io
```

## Limitations

- Requires `root@pam` API token (standard PVE user tokens will fail)
- `ListSnapshots` is not implemented; snapshot listing in `kubectl` is limited
- This is an experimental feature and may be removed or changed in future releases
