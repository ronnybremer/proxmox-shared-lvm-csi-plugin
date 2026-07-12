"""
Configuration management for Proxmox CSI Driver
"""
import yaml
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ProxmoxCluster:
    """Proxmox cluster configuration"""
    url: str
    region: str
    # Either token_id/token_secret or username/password must be set.
    # If username/password are set, they take precedence and token_id/token_secret are ignored.
    token_id: Optional[str] = None
    token_secret: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
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

    Example config.yaml (token authentication):
        clusters:
          - url: "https://proxmox.example.com:8006/api2/json"
            token_id: "csi@pve!csi-token"
            token_secret: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            region: "cluster-1"
            insecure: false

    Example config.yaml (username/password authentication):
        clusters:
          - url: "https://proxmox.example.com:8006/api2/json"
            username: "root@pam"
            password: "xxxxxxxx"
            region: "cluster-1"
            insecure: false
    """
    with open(config_path, 'r') as f:
        data = yaml.safe_load(f)

    clusters = []
    for cluster_data in data.get('clusters', []):
        region = cluster_data['region']
        username = cluster_data.get('username')
        password = cluster_data.get('password')

        if username and password:
            cluster = ProxmoxCluster(
                url=cluster_data['url'],
                region=region,
                username=username,
                password=password,
                insecure=cluster_data.get('insecure', False)
            )
        else:
            token_id = cluster_data.get('token_id')
            token_secret = cluster_data.get('token_secret')
            if not token_id or not token_secret:
                raise ValueError(
                    f"Cluster '{region}' must configure either "
                    "'username'/'password' or 'token_id'/'token_secret'"
                )
            cluster = ProxmoxCluster(
                url=cluster_data['url'],
                region=region,
                token_id=token_id,
                token_secret=token_secret,
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
