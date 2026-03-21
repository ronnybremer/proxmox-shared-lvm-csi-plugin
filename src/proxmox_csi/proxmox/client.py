"""
Proxmox REST API Client

Implements direct REST API calls to Proxmox VE using token authentication.
"""
import requests
import logging
from typing import Dict, Any, Optional, List
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning


logger = logging.getLogger(__name__)


class ProxmoxClient:
    """Proxmox VE REST API Client"""

    def __init__(self, url: str, token_id: str, token_secret: str, insecure: bool = False):
        """
        Initialize Proxmox API client

        Args:
            url: Base URL to Proxmox API (e.g., https://proxmox.example.com:8006/api2/json)
            token_id: API token ID (e.g., csi@pve!csi-token)
            token_secret: API token secret
            insecure: Skip TLS certificate verification
        """
        self.base_url = url.rstrip('/api2/json').rstrip('/')
        self.api_url = f"{self.base_url}/api2/json"
        self.token_id = token_id
        self.token_secret = token_secret
        self.verify = not insecure

        if insecure:
            disable_warnings(InsecureRequestWarning)

        # Setup session with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.3,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)

        # Set authorization header
        self.session.headers.update({
            'Authorization': f'PVEAPIToken={token_id}={token_secret}',
            'Content-Type': 'application/json'
        })

    def _request(self, method: str, path: str, data: Optional[Dict] = None,
                 params: Optional[Dict] = None) -> Any:
        """
        Make REST API request

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            path: API path (e.g., /nodes/pve-1/qemu/100/config)
            data: Request body data
            params: Query parameters

        Returns:
            Response data

        Raises:
            requests.exceptions.HTTPError: On HTTP error
        """
        url = f"{self.api_url}{path}"

        logger.debug(f"Proxmox API Request: {method} {path}")
        if data:
            logger.debug(f"Proxmox API Request body: {data}")
        if params:
            logger.debug(f"Proxmox API Request params: {params}")

        response = self.session.request(
            method,
            url,
            json=data,
            params=params,
            verify=self.verify
        )

        logger.debug(f"Proxmox API Response: status={response.status_code}")

        response.raise_for_status()

        result = response.json()
        logger.debug(f"Proxmox API Response data: {result}")
        return result.get('data', result)

    def get_nodes(self) -> List[str]:
        """
        Get list of cluster nodes

        Returns:
            List of node names
        """
        nodes = self._request('GET', '/nodes')
        return [node['node'] for node in nodes]

    def get_vms(self, node: str) -> List[Dict]:
        """
        Get list of VMs on a node

        Args:
            node: Node name

        Returns:
            List of VM dictionaries
        """
        return self._request('GET', f'/nodes/{node}/qemu')

    def get_vm_config(self, vmid: int, node: str) -> Dict:
        """
        Get VM configuration

        Args:
            vmid: VM ID
            node: Node name

        Returns:
            VM configuration dictionary
        """
        return self._request('GET', f'/nodes/{node}/qemu/{vmid}/config')

    def update_vm_config(self, vmid: int, node: str, config: Dict) -> Dict:
        """
        Update VM configuration

        Args:
            vmid: VM ID
            node: Node name
            config: Configuration parameters

        Returns:
            Task response
        """
        return self._request('POST', f'/nodes/{node}/qemu/{vmid}/config', data=config)

    def create_vm_disk(self, vmid: int, node: str, storage: str,
                      filename: str, size_bytes: int) -> Dict:
        """
        Create VM disk

        Args:
            vmid: VM ID
            node: Node name
            storage: Storage ID
            filename: Disk filename
            size_bytes: Size in bytes

        Returns:
            Response data
        """
        size_gb = size_bytes / (1024**3)

        data = {
            'vmid': vmid,
            'filename': filename,
            'size': f'{int(size_gb)}G'
        }

        logger.info(f"create_vm_disk: vmid={vmid}, node={node}, storage={storage}, "
                   f"filename={filename}, size_bytes={size_bytes}, size_gb={size_gb:.2f}")
        logger.debug(f"create_vm_disk: POST /nodes/{node}/storage/{storage}/content with data={data}")

        return self._request('POST', f'/nodes/{node}/storage/{storage}/content',
                           data=data)

    def delete_vm_disk(self, vmid: int, node: str, storage: str, volume: str) -> Dict:
        """
        Delete VM disk

        Args:
            vmid: VM ID
            node: Node name
            storage: Storage ID
            volume: Volume name

        Returns:
            Response data
        """
        return self._request('DELETE',
                           f'/nodes/{node}/storage/{storage}/content/{volume}')

    def resize_vm_disk(self, vmid: int, node: str, device: str, size: str) -> Dict:
        """
        Resize VM disk

        Args:
            vmid: VM ID
            node: Node name
            device: Device name (e.g., scsi0)
            size: Size string (e.g., +10G or 500M)

        Returns:
            Task response
        """
        data = {
            'disk': device,
            'size': size
        }

        return self._request('PUT', f'/nodes/{node}/qemu/{vmid}/resize', data=data)

    def copy_volume(self, node: str, storage: str, volume: str,
                    target_name: str, target_node: Optional[str] = None) -> Dict:
        """
        Copy volume (for snapshots/clones)

        EXPERIMENTAL: Requires root@pam authentication (not standard API tokens).

        Args:
            node: Source node name
            storage: Storage ID
            volume: Source volume name
            target_name: Target volume name
            target_node: Target node (optional, for cross-node copy)

        Returns:
            Task response
        """
        data = {'target': target_name}
        if target_node:
            data['target_node'] = target_node

        return self._request('POST',
                             f'/nodes/{node}/storage/{storage}/content/{volume}',
                             data=data)

    def get_storage_config(self, storage: str) -> Dict:
        """
        Get storage configuration

        Args:
            storage: Storage ID

        Returns:
            Storage configuration
        """
        storages = self._request('GET', '/storage')
        for stor in storages:
            if stor.get('storage') == storage:
                return stor
        raise ValueError(f"Storage {storage} not found")

    def extract_scsi_disks(self, vm_config: Dict) -> Dict[str, str]:
        """
        Extract SCSI disks from VM configuration

        Args:
            vm_config: VM configuration dictionary

        Returns:
            Dictionary mapping device names to disk strings
        """
        scsi_disks = {}
        for key, value in vm_config.items():
            if key.startswith('scsi') and isinstance(value, str):
                scsi_disks[key] = value
        return scsi_disks

    def find_vm_by_name(self, vm_name: str) -> Optional[tuple[int, str]]:
        """
        Find VM ID by hostname/name

        Scans all nodes in the cluster to find a VM matching the given name.

        Args:
            vm_name: VM hostname/name to search for

        Returns:
            Tuple of (vmid, node) if found, None otherwise
        """
        logger.info(f"Searching for VM with name: {vm_name}")

        nodes = self.get_nodes()
        for node in nodes:
            try:
                vms = self.get_vms(node)
                for vm in vms:
                    vmid = vm.get('vmid')
                    name = vm.get('name', '')

                    # Check if name matches (case-insensitive)
                    if name.lower() == vm_name.lower():
                        logger.info(f"Found VM {vmid} on node {node} with name {name}")
                        return (vmid, node)

            except Exception as e:
                logger.error(f"Failed to query VMs on node {node}: {e}")
                continue

        logger.warning(f"No VM found with name: {vm_name}")
        return None

    def find_vm_node(self, vmid: int) -> Optional[str]:
        """
        Find which node a VM is running on

        Args:
            vmid: VM ID to search for

        Returns:
            Node name if found, None otherwise
        """
        logger.debug(f"Searching for node hosting VM {vmid}")

        nodes = self.get_nodes()
        for node in nodes:
            try:
                vms = self.get_vms(node)
                for vm in vms:
                    if vm.get('vmid') == vmid:
                        logger.debug(f"Found VM {vmid} on node {node}")
                        return node

            except Exception as e:
                logger.error(f"Failed to query VMs on node {node}: {e}")
                continue

        logger.warning(f"VM {vmid} not found on any node")
        return None
