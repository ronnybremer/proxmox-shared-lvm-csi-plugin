"""
Configuration management for Proxmox CSI Driver
"""
import yaml
from dataclasses import dataclass
from typing import List


@dataclass
class ProxmoxCluster:
    """Proxmox cluster configuration"""
    url: str
    token_id: str
    token_secret: str
    region: str
    insecure: bool = False


@dataclass
class CSIConfig:
    """CSI driver configuration"""
    clusters: List[ProxmoxCluster]
    enable_experimental_snapshots: bool = False


def load_config(config_path: str) -> CSIConfig:
    """
    Load configuration from YAML file

    Args:
        config_path: Path to YAML configuration file

    Returns:
        CSIConfig object

    Example config.yaml:
        clusters:
          - url: "https://proxmox.example.com:8006/api2/json"
            token_id: "csi@pve!csi-token"
            token_secret: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            region: "cluster-1"
            insecure: false
    """
    with open(config_path, 'r') as f:
        data = yaml.safe_load(f)

    clusters = []
    for cluster_data in data.get('clusters', []):
        cluster = ProxmoxCluster(
            url=cluster_data['url'],
            token_id=cluster_data['token_id'],
            token_secret=cluster_data['token_secret'],
            region=cluster_data['region'],
            insecure=cluster_data.get('insecure', False)
        )
        clusters.append(cluster)

    if not clusters:
        raise ValueError("No clusters configured")

    enable_experimental_snapshots = data.get('enable_experimental_snapshots', False)

    return CSIConfig(
        clusters=clusters,
        enable_experimental_snapshots=enable_experimental_snapshots
    )
