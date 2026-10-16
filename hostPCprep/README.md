# system
brain pc needs some stuff to work fine...

## base = debian 13.7 arm64 

echo "deb http://deb.debian.org/debian trixie-backports main contrib non-free non-free-firmware" | sudo tee -a /etc/apt/sources.list

## firmware
extra firmwarwe needed :
wget https://github.com/cixtech/cix_proprietary__cix_proprietary/raw/refs/heads/cix_p1_k6.6_master/cix_proprietary-debs/cix-audio-dsp/usr/lib/firmware/dsp_fw.bin

sudo install -Dm645 dsp_fw.bin /lib/firmware/cix/dsp_fw.bin


## userspace driver
sudo apt install   mesa-vulkan-drivers   libgl1-mesa-dri   libegl1-mesa-dev   libglx-mesa0   mesa-utils   vulkan-tools


# labwc instead of gnome
sudo apt install labwc anshi  mako-notifier  swaybg  swayidle  waybar


# enable vnc
sudo apt install wayvnc
cp files from ./config >> ~/.config

## enable user service
systemctl --user daemonreload

## robot-lab network (kuka-net.sh)
builds the KUKA robot-lab network (fixed Kuka IP + RPi link + AX210 AP + BELA).

```
sudo ./kuka-net.sh discover          # show iface names, fill in the CONFIG section
sudo ./kuka-net.sh install           # one-time: hostapd dnsmasq iw etc.
sudo ./kuka-net.sh enable            # build it — TEMPORARY, reverts on reboot
sudo ./kuka-net.sh enable --persist  # same, but survives reboot (boot service + sysctl)
sudo ./kuka-net.sh status            # current state (enabled / persist / runtime)
sudo ./kuka-net.sh disable           # tear down + restore standard DHCP
sudo ./kuka-net.sh reset             # force-restore standard config
```

- default `enable` is temporary — if you lose connectivity, a reboot restores DHCP (no stuck config).
- `--persist` adds a boot service (`kuka-net.service`) + `ip_forward` sysctl so it rebuilds after reboot; `disable` removes both.
- save / restore the current setup to a file for easy reverting:
```
sudo ./kuka-net.sh save /tmp/lab.conf
sudo ./kuka-net.sh restore /tmp/lab.conf
```