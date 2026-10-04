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

## talosm01

| Adapter  | VLAN      | Subnet          | Decription                         | IP            |
| -------- | --------- | --------------- | ---------------------------------- | ------------- |
| eth0     | Default   | 192.168.0.0/24  | Home network, only here for Sonos  | dhcp          |
| eth1     | IoT       | 192.168.50.0/24 | Isolated IoT Network               | dhcp          |
| eth2     | Hayneslab | 192.168.40.0/24 | Rack network                       | 192.168.40.93 |
| eth3     | RookLan   | 192.168.60.0/24 | Ceph cluster private network       | dhcp          |

## talosm02

| Adapter  | VLAN      | Subnet          | Decription                         | IP            |
| -------- | --------- | --------------- | ---------------------------------- | ------------- |
| eth0     | Default   | 192.168.0.0/24  | Home network, only here for Sonos  | dhcp          |
| eth1     | IoT       | 192.168.50.0/24 | Isolated IoT Network               | dhcp          |
| eth2     | Hayneslab | 192.168.40.0/24 | Rack network                       | 192.168.40.59 |
| eth3     | RookLan   | 192.168.60.0/24 | Ceph cluster private network       | dhcp          |

## talosm03

| Adapter  | VLAN      | Subnet          | Decription                         | IP            |
| -------- | --------- | --------------- | ---------------------------------- | ------------- |
| eth0     | Default   | 192.168.0.0/24  | Home network, only here for Sonos  | dhcp          |
| eth1     | IoT       | 192.168.50.0/24 | Isolated IoT Network               | dhcp          |
| eth2     | Hayneslab | 192.168.40.0/24 | Rack network                       | 192.168.40.10 |
| eth3     | RookLan   | 192.168.60.0/24 | Ceph cluster private network       | dhcp          |

## talosm04

Ex-edgem03 (#3332). Pro Aggregation SFP+ 7 / SFP+ 16, Pro Max 24 PoE ports 12 / 16.

| Adapter  | VLAN      | Subnet          | Decription                         | IP            |
| -------- | --------- | --------------- | ---------------------------------- | ------------- |
| eth0     | Default   | 192.168.0.0/24  | Home network, only here for Sonos  | dhcp          |
| eth1     | IoT       | 192.168.50.0/24 | Isolated IoT Network               | dhcp          |
| eth2     | Hayneslab | 192.168.40.0/24 | Rack network                       | dhcp (.6)     |
| eth3     | RookLan   | 192.168.60.0/24 | Ceph cluster private network       | dhcp          |

## talosm05

Ex-edgem01 (#3332). Pro Aggregation SFP+ 19 / SFP+ 20, Flex 2.5G 8 PoE ports 3 (eth0) / 2 (eth1).

| Adapter  | VLAN      | Subnet          | Decription                         | IP            |
| -------- | --------- | --------------- | ---------------------------------- | ------------- |
| eth0     | Default   | 192.168.0.0/24  | Home network, only here for Sonos  | dhcp          |
| eth1     | IoT       | 192.168.50.0/24 | Isolated IoT Network               | dhcp          |
| eth2     | Hayneslab | 192.168.40.0/24 | Rack network                       | dhcp (.79)    |
| eth3     | RookLan   | 192.168.60.0/24 | Ceph cluster private network       | dhcp          |

## talosw01

| Adapter  | VLAN      | Subnet          | Decription                         | IP            |
| -------- | --------- | --------------- | ---------------------------------- | ------------- |
| eth0     | Hayneslab | 192.168.40.0/24 | Rack network                       | dhcp          |
| eth1     | VPN       | 192.168.30.0/24 | VPN Network                        | dhcp          |

## talosw02

| Adapter  | VLAN      | Subnet          | Decription                         | IP            |
| -------- | --------- | --------------- | ---------------------------------- | ------------- |
| eth0     | Hayneslab | 192.168.40.0/24 | Rack network                       | dhcp          |
| eth1     | VPN       | 192.168.30.0/24 | VPN Network                        | dhcp          |

## talosw03

| Adapter  | VLAN      | Subnet          | Decription                         | IP            |
| -------- | --------- | --------------- | ---------------------------------- | ------------- |
| eth0     | Hayneslab | 192.168.40.0/24 | Rack network                       | dhcp          |
| eth1     | VPN       | 192.168.30.0/24 | VPN Network                        | dhcp          |

## talosw04

The eGPU test node (ex-edgew01). One cabled NIC, no macvlan networks.

| Adapter  | VLAN      | Subnet          | Decription                         | IP            |
| -------- | --------- | --------------- | ---------------------------------- | ------------- |
| eth0     | Hayneslab | 192.168.40.0/24 | Rack network                       | dhcp          |
| eth1     | (no link) |                 |                                    |               |
