# Home Automation

These documents are going to focus more on getting services up an running and less about what hardware and automations I'm using. That shall be documented elsewhere.

For now this will be a placeholder / TODO list...

- Flash [Tubesz Z-Wave PoE Kit](https://tubeszb.com/product/z-wave-poe-kit/) with firmware [zw-kit](https://github.com/tube0013/tube_gateways/tree/main/models/current/tubeszb-zw-kit)
- Flash [TubesZB EFR32 MGM24 PoE Coordinator 2024](https://tubeszb.com/product/efr32-mgm24-poe-coordinator/) with firmware [efr32-MGM24](https://github.com/tube0013/tube_gateways/tree/main/models/current/tubeszb-efr32-MGM24/firmware)

## Flash Zigbee EFR32 Coordinatior

I believe there are two pieces to the firmware - one is the silicon labs coordinator while the other is the ESP32 firmware on the board running it.

### Silicon Labs for Zigbee

1. Install [HAOS Addon](https://github.com/tube0013/tubeszb_addons) (while we stil can) on the VM instance
1. Use IP/Port for device -> 192.168.50.162:6638
1. Click usb device even though it's not usb
1. Grab the link to the 'raw' view of the file in gitlab, in this case https://github.com/tube0013/tube_gateways/raw/refs/heads/main/models/current/tubeszb-efr32-MGM24/firmware/mgm24/ncp/4.4.3/tubesZB-EFR32-MGM24_NCP_7.4.3.gbl - the link the the file itself is just a webpage
1. Use default baudrate 
1. Verbose mode becasue why not
1. Save
1. Accept the restart
1. Watch logs

Looks fine but we won't know until we get Z2M back alive:

```
[21:43:11] INFO: universal-silabs-flasher-up script exited with code 0
s6-rc: info: service universal-silabs-flasher successfully started
s6-rc: info: service legacy-services: starting
s6-rc: info: service legacy-services successfully started
s6-rc: info: service legacy-services: stopping
s6-rc: info: service legacy-services successfully stopped
s6-rc: info: service universal-silabs-flasher: stopping
s6-rc: info: service universal-silabs-flasher successfully stopped
s6-rc: info: service banner: stopping
s6-rc: info: service banner successfully stopped
s6-rc: info: service legacy-cont-init: stopping
s6-rc: info: service legacy-cont-init successfully stopped
s6-rc: info: service fix-attrs: stopping
s6-rc: info: service fix-attrs successfully stopped
s6-rc: info: service s6rc-oneshot-runner: stopping
s6-rc: info: service s6rc-oneshot-runner successfully stopped
```

### ESPHome for Zigbee

> **Hostname** tubeszb-zigbee01.local

Looks ready to go on `tubeszb-zigbee01.haynesnetwork:6053` though 6638 is the port for zigbee so not sure what 6638 is for. Has a wabsite at `http://tubeszb-zigbee01.haynesnetwork/`

## Flash Z-Wave Kit

### Zooz Radeo Firmware

ZWave JS UI is complainign that it want's Z-Wave SDK 7.22.1 or greater so we better do this one!

Get the file from Zooz, at the time I did so it was [here](https://www.support.getzooz.com/kb/article/1158-zooz-ota-firmware-files/) and called `https://www.getzooz.com/firmware/ZAC93_SDK_7.22.1_US-LR_V01R50.zip`. Note that I have model number `ZAC93` which is how I found this file.

Extract the zip and verify it's a .gbl file. Then go to the device in ZWave JS UI -> Advanced -> Firmware update OTW and select the file.

> Note that the [release notes](https://www.support.getzooz.com/kb/article/1389-zac93-gpio-module-change-log/) stopped being updated at 1.20 but here in October we got 1.50

### ESPHome for Z Wave

> **Hostname** tubeszb-zwave01.local

This looks different and the firmware files look esphome related. I will use esphome to update it!

ESPHome discovered both of these guys. After clicking Adop it just rocked and rolled. Some encryption key was spat out and I had the option to update it.

Well, update upataded, and it seemed to point at the repo I was going to go to anyway. Now it has a website! `http://tubeszb-zwave01.haynesnetwork` and `tubeszb-zwave01.haynesnetwork:6638` seems ready to go... Guess I'll delete it for now and see how setting other stuff up goes.