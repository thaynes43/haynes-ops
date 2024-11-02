# Checklist

## Router

- [ ] Setup same subnets and VLANs
- [ ] Setup HNET+ WiFi for 5Ghz and 6Ghz 
- [ ] Setup HNETIoT WiFi for 2.4Ghz and in the IoT VLAN. Only add 2.4Ghz - make sure the password matches (in esphome 1Password)
- [ ] Channel Width 20 for 2.4 Ghz, TBD if we use Auto for channel
- [ ] Channel Width 80 for 5Ghz & Auto Channel
- [ ] Channel Width 160 for 6Ghz & Auto Channel 
- [ ] Sonos Hoops
- [ ] Host (A) DNS for tubeszb-zigbee01.haynesnetwork -> 192.168.50.162
- [ ] Host (A) DNS for tubeszb-zwave01.haynesnetwork -> 192.168.50.92
- [ ] Host (A) DNS for internal.haynesops -> 192.168.40.203
- [ ] Setup external for cloudflare and forward ports etc (TODO document this)

> **TODO** Review the rest of the DNS stuff

- [ ] Sort out Talos01-03 IPs

# Appendix 

### VLANs

| VLAN # | Subnet          | Name        | Decription                                   | 
| 1      | 192.168.0.0/24  | Default     |                                              |
| 2      | 192.168.20.0/24 | CephLan     | Isolated, no internet, for proxmox ceph only |
| 3      | 192.168.30.0/24 | VPNLan      | Bound to https://mullvad.net/en              |
| 4      | 192.168.40.0/24 | Hayneslab   | Configued wrt k8s loadbalacer pools          |
| 5      | 192.168.50.0/24 | IoT         | TODO needs work to be better isolate         | 
| 6      | 192.168.60.0/24 | RookLan     | Isolated, no internet, for rook-ceph only    |

