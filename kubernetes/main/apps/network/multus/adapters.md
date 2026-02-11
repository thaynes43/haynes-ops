# VLANs

| VLAN # | Subnet          | Name        | Decription                                   | 
| 1      | 192.168.0.0/24  | Default     |                                              |
| 2      | 192.168.20.0/24 | CephLan     | Isolated, no internet, for proxmox ceph only |
| 3      | 192.168.30.0/24 | VPNLan      | Bound to https://mullvad.net/en              |
| 4      | 192.168.40.0/24 | Hayneslab   | Configued wrt k8s loadbalacer pools          |
| 5      | 192.168.50.0/24 | IoT         | TODO needs work to be better isolate         | 
| 6      | 192.168.60.0/24 | RookLan     | Isolated, no internet, for rook-ceph only    |

# Network Adapters

> **NOTE** The kernel parameter `net.ifnames=0` is used for the hosts below as ones with GPUs were named differently with the friendly names.

Reminder of what network adapters are where.

## taslosm01

| Adapter  | VLAN      | Subnet          | Decription                         | IP            |
| -------- | --------- | --------------- | ---------------------------------- | ------------- |
| eth0     | Default   | 192.168.0.0/24  | Home network, only here for Sonos  | dhcp          |
| eth1     | IoT       | 192.168.50.0/24 | Isolated IoT Network               | dhcp          |
| eth2     | Hayneslab | 192.168.40.0/24 | Rack network                       | 192.168.40.93 |
| eth3     | RookLan   | 192.168.60.0/24 | Ceph cluster private network       | dhcp          |

## taslosm02

| Adapter  | VLAN      | Subnet          | Decription                         | IP            |
| -------- | --------- | --------------- | ---------------------------------- | ------------- |
| eth0     | Default   | 192.168.0.0/24  | Home network, only here for Sonos  | dhcp          |
| eth1     | IoT       | 192.168.50.0/24 | Isolated IoT Network               | dhcp          |
| eth2     | Hayneslab | 192.168.40.0/24 | Rack network                       | 192.168.40.59 |
| eth3     | RookLan   | 192.168.60.0/24 | Ceph cluster private network       | dhcp          |

## taslosm03

| Adapter  | VLAN      | Subnet          | Decription                         | IP            |
| -------- | --------- | --------------- | ---------------------------------- | ------------- |
| eth0     | Default   | 192.168.0.0/24  | Home network, only here for Sonos  | dhcp          |
| eth1     | IoT       | 192.168.50.0/24 | Isolated IoT Network               | dhcp          |
| eth2     | Hayneslab | 192.168.40.0/24 | Rack network                       | 192.168.40.10 |
| eth3     | RookLan   | 192.168.60.0/24 | Ceph cluster private network       | dhcp          |

## taslosw01

| Adapter  | VLAN      | Subnet          | Decription                         | IP            |
| -------- | --------- | --------------- | ---------------------------------- | ------------- |
| eth0     | Default   | 192.168.0.0/24  | Home network, only here for Sonos  | dhcp          |
| eth1     | VPN       | 192.168.30.0/24 |  VPN Network                       | dhcp          |

## taslosw02

| Adapter  | VLAN      | Subnet          | Decription                         | IP            |
| -------- | --------- | --------------- | ---------------------------------- | ------------- |
| eth0     | Default   | 192.168.0.0/24  | Home network, only here for Sonos  | dhcp          |
| eth1     | VPN       | 192.168.30.0/24 |  VPN Network                       | dhcp          |

## taslosw03

| Adapter  | VLAN      | Subnet          | Decription                         | IP            |
| -------- | --------- | --------------- | ---------------------------------- | ------------- |
| eth0     | Default   | 192.168.0.0/24  | Home network, only here for Sonos  | dhcp          |
| eth1     | VPN       | 192.168.30.0/24 |  VPN Network                       | dhcp          |