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