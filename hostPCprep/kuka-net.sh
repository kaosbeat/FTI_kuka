#!/usr/bin/env bash
#
# kuka-net.sh — Enable/disable the KUKA robot-lab network on the SBC.
#
# The SBC (Debian trixie) has two ethernet ports + an Intel AX210 wifi.
#
#   enable  -> builds the robot-lab network:
#       Port 1 (Kuka LAN)  192.168.10.111/24   FIXED — the KRC (192.168.10.201) dials in
#       Port 2 (RPi link)  192.168.10.112/24   RPi 5 at 192.168.10.113
#       USB gadget (BELA)  kept as-is (its own DHCP) — NATed so the AP can reach bela.local
#       AX210 Access Point 192.168.20.1/24     TB20plus + your PC
#     IP forwarding + NAT so the AP can reach VNC (RPi/SBC) and http://bela.local.
#     The AP is on its OWN subnet so it never shares the Kuka's real-time segment.
#
#   disable -> tears all of that down and restores the standard DHCP config.
#
# Usage (as root):
#   sudo ./kuka-net.sh discover     # show interfaces so you can fill in the CONFIG
#   sudo ./kuka-net.sh install      # one-time: install hostapd/dnsmasq/etc.
#   sudo ./kuka-net.sh enable       # build the robot-lab network
#   sudo ./kuka-net.sh disable      # revert to the standard config
#   sudo ./kuka-net.sh status       # show current state
#   sudo ./kuka-net.sh reset        # force-restore the saved standard config
#
set -uo pipefail

# ============================================================
#  CONFIG — edit to match your SBC. Run `discover` first.
# ============================================================
# --- Interfaces (run `discover` to see the real names) ---
IF_KUKA="enp15s0"        # ethernet port -> Kuka robot LAN
IF_RPI="enp24s0"         # ethernet port -> RPi 5
IF_BEAGLE=""             # USB gadget -> BeagleBone/BELA; "" = autodetect
IF_AP="wlp17s0"          # Intel AX210 wifi -> Access Point

# --- Kuka robot LAN (fixed) ---
KUKA_IP="192.168.10.111" # FIXED — the KRC connects to this exact address
KUKA_NET="192.168.10.0/24"
KUKA_DEV="192.168.10.201" # KUKA robot (informational)

# --- RPi link (shares 192.168.10.0/24) ---
RPI_PORT_IP="192.168.10.112"  # SBC side of the RPi port
RPI_IP="192.168.10.113"       # your RPi 5 (edit to match)

# --- BeagleBone / BELA (USB gadget) ---
# The BeagleBone ALREADY works in your standard config: it runs its own USB
# gadget DHCP server. So we do NOT reconfigure its IP — we detect the gadget
# interface, keep it up, and NAT the AP to it so your PC can reach bela.local.
BEAGLE_IF=""                  # USB gadget iface on the SBC ("" = autodetect usb0)
BEAGLE_NAME="bela"            # IDE name -> http://bela.local
BEAGLE_IP=""                  # BeagleBone IP ("" = auto; else pin bela.local here)

# --- Access Point (AX210) ---
AP_SSID="kuka-net"
AP_PASS="kuka-net-2024"
AP_NET="192.168.20.0/24"
AP_IP="192.168.20.1"
AP_DHCPRANGE="192.168.20.10,192.168.20.250"
AP_CHANNEL="6"
AP_COUNTRY="DE"

# --- Ports the firewall must open ---
# Kuka EKI dials in on 18735; the core serves WS 8765 + HTTP 8766; VNC 5900/5901;
# SSH 22; the BELA IDE on 80.
RT_PORTS="18735 8765 8766 5900 5901 22 80"

# --- State ---
STATE_DIR="/etc/kuka-net"
BACKUP_DIR="$STATE_DIR/backup"
MARKER="$STATE_DIR/enabled"
LOG="$STATE_DIR/kuka-net.log"

# --- Options ---
DISABLE_TAILSCALE=1        # 1 = stop tailscale during enable (routing isolation)
ENABLE_AP=1                # 1 = build the AX210 access point (0 = skip it)

# ============================================================
#  Helpers
# ============================================================
log(){ printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG" 2>/dev/null || printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die(){ echo "ERROR: $*" >&2; exit 1; }
need_root(){ [[ $EUID -eq 0 ]] || die "run as root:  sudo $0 $1"; }
is_enabled(){ [[ -f "$MARKER" ]]; }

detect_manager(){
  if systemctl is-active NetworkManager >/dev/null 2>&1;   then echo nm
  elif systemctl is-active systemd-networkd >/dev/null 2>&1; then echo networkd
  else echo ifup
  fi
}

# Find the USB gadget interface backing the BeagleBone.
detect_beagle(){
  local if dev
  for if in $(ls /sys/class/net/ 2>/dev/null); do
    dev=$(readlink -f "/sys/class/net/$if/device" 2>/dev/null || true)
    if grep -qi 'usb' <<<"$dev"; then echo "$if"; return; fi
  done
  local cand
  for cand in $(ls /sys/class/net/ 2>/dev/null); do
    [[ "$cand" =~ ^usb[0-9]*$ ]] && { echo "$cand"; return; }
  done
  echo ""
}

iface_ip(){ ip -4 addr show "$1" 2>/dev/null | awk '/inet /{print $2; exit}'; }
subnet_of(){ echo "${1%.*}.0/24"; }  # 192.168.7.2 -> 192.168.7.0/24

# ============================================================
#  discover — print the interfaces so you can fill in the CONFIG
# ============================================================
cmd_discover(){
  echo "=== Network interfaces ==="
  for if in $(ls /sys/class/net/ 2>/dev/null); do
    echo "--- $if ---"
    echo "  driver : $(basename $(readlink /sys/class/net/$if/device/driver 2>/dev/null) 2>/dev/null || echo '?')"
    echo "  device : $(readlink -f /sys/class/net/$if/device 2>/dev/null || echo '?')"
    echo "  carrier: $(cat /sys/class/net/$if/carrier 2>/dev/null || echo '?')"
    echo "  inet   : $(iface_ip "$if" || echo 'none')"
    echo "  up     : $(ip link show "$if" 2>/dev/null | grep -q 'state UP' && echo yes || echo no)"
  done
  echo
  echo "=== USB devices (look for the BeagleBone gadget) ==="
  lsusb 2>/dev/null | grep -viE 'root hub|hub$' || echo "(none)"
  echo
  echo "=== Detected USB-gadget interface (BeagleBone) ==="
  echo "  $(detect_beagle || echo 'none found')"
  echo
  echo "=== Wifi (AX210) ==="
  iw dev 2>/dev/null || echo "(iw not installed)"
  echo
  echo "=== Network manager in use ==="
  echo "  $(detect_manager)"
  echo
  echo "=== Tailscale / Docker (may need disabling for isolation) ==="
  systemctl is-active tailscaled 2>/dev/null | sed 's/^/  tailscale: /'
  systemctl is-active docker     2>/dev/null | sed 's/^/  docker:    /'
  echo
  echo "HINT: put the interface names above into the CONFIG section of this script."
  echo "      Port 1 (Kuka) = the port with the cable to the Kuka robot."
  echo "      Port 2 (RPi)  = the port with the cable to the RPi 5."
}

# ============================================================
#  install — one-time package setup
# ============================================================
cmd_install(){
  apt-get update
  apt-get install -y hostapd dnsmasq iw iproute2 iptables avahi-daemon
  mkdir -p "$STATE_DIR"
  log "installed: hostapd dnsmasq iw iproute2 iptables avahi-daemon"
  log "state dir: $STATE_DIR"
}

# ============================================================
#  snapshot — save the current (standard) state for a clean revert
# ============================================================
snapshot(){
  rm -rf "$BACKUP_DIR"; mkdir -p "$BACKUP_DIR"
  ip a save > "$BACKUP_DIR/ip-links" 2>/dev/null || ip a > "$BACKUP_DIR/ip-a"
  ip route > "$BACKUP_DIR/ip-route" 2>/dev/null || true
  cp -a /etc/NetworkManager/system-connections "$BACKUP_DIR/nm-sysconns" 2>/dev/null || true
  [[ -f /etc/network/interfaces ]] && cp -a /etc/network/interfaces "$BACKUP_DIR/interfaces"
  [[ -d /etc/systemd/network ]]    && cp -a /etc/systemd/network "$BACKUP_DIR/networkd"
  cp -a /etc/hosts "$BACKUP_DIR/hosts" 2>/dev/null || true
  cp -a /etc/resolv.conf "$BACKUP_DIR/resolv.conf" 2>/dev/null || true
  echo "$(detect_manager)" > "$BACKUP_DIR/manager"
  echo "kuka=$IF_KUKA rpi=$IF_RPI ap=$IF_AP beagle=${IF_BEAGLE:-auto}" > "$BACKUP_DIR/ifaces"
  # capture the BeagleBone gadget iface + its current IP (so we can keep it up)
  local bif; bif="${IF_BEAGLE:-$(detect_beagle)}"
  echo "$bif" > "$BACKUP_DIR/beagle-if"
  echo "$(iface_ip "$bif" 2>/dev/null)" > "$BACKUP_DIR/beagle-ip"
  log "snapshot saved to $BACKUP_DIR (manager: $(detect_manager))"
}

# ============================================================
#  enable — build the robot-lab network
# ============================================================
cmd_enable(){
  is_enabled && { log "already enabled (marker present); re-applying."; }
  mkdir -p "$STATE_DIR"
  snapshot

  # --- stop the config manager so it doesn't fight our static config ---
  local mgr; mgr=$(detect_manager)
  case "$mgr" in
    nm)       systemctl stop NetworkManager ;;
    networkd) systemctl stop systemd-networkd ;;
    ifup)     : ;;
  esac
  [[ "$DISABLE_TAILSCALE" == 1 ]] && systemctl stop tailscaled 2>/dev/null || true
  log "stopped manager: $mgr"

  # --- Kuka port (fixed, no default route) ---
  ip link set "$IF_KUKA" up
  ip addr flush dev "$IF_KUKA" 2>/dev/null || true
  ip addr add "$KUKA_IP/24" dev "$IF_KUKA"
  log "Kuka port  $IF_KUKA -> $KUKA_IP/24"

  # --- RPi port ---
  ip link set "$IF_RPI" up
  ip addr flush dev "$IF_RPI" 2>/dev/null || true
  ip addr add "$RPI_PORT_IP/24" dev "$IF_RPI"
  log "RPi port   $IF_RPI -> $RPI_PORT_IP/24"

  # --- BeagleBone USB gadget (keep the existing gadget link alive) ---
  local bif bip; bif=$(cat "$BACKUP_DIR/beagle-if" 2>/dev/null || echo "${IF_BEAGLE:-$(detect_beagle)}")
  bip=$(cat "$BACKUP_DIR/beagle-ip" 2>/dev/null)
  if [[ -n "$bif" && -n "$bip" ]]; then
    ip link set "$bif" up
    ip addr del "$bip" dev "$bif" 2>/dev/null || true
    ip addr add "$bip" dev "$bif"
    echo "$bif" > "$STATE_DIR/beagle-if"
    log "BeagleBone $bif -> $bip (gadget link kept, subnet $(subnet_of "$bip"))"
  else
    log "WARN: no USB gadget IP captured for the BeagleBone; bela.local may be unreachable from the AP"
  fi

  # --- Access Point (AX210) ---
  if [[ "$ENABLE_AP" == 1 ]]; then
    cmd_apply_ap
  fi

  # --- routing + NAT + firewall ---
  cmd_apply_forward

  # --- DNS for the SBC itself (bela.local) ---
  local bip belabone; bip=$(cat "$BACKUP_DIR/beagle-ip" 2>/dev/null)
  belabone="${BEAGLE_IP:-${bip%.*}.1}"
  grep -q "bela.local" /etc/hosts || echo "$belabone  $BEAGLE_NAME.local" >> /etc/hosts

  # --- mark enabled ---
  echo "$mgr" > "$MARKER"
  log "ENABLED. Kuka=$KUKA_IP  RPi=$RPI_IP  Beagle=$belabone  AP=$AP_SSID ($AP_IP)"
}

# hostapd + dnsmasq for the AP (standalone LAN for your PC + TB20plus)
cmd_apply_ap(){
  # stop any system-managed hostapd/dnsmasq so our instances own the radio + port 53
  systemctl stop hostapd dnsmasq 2>/dev/null || true
  pkill -f "hostapd.*kuka-net" 2>/dev/null || true
  pkill -f "dnsmasq.*kuka-net" 2>/dev/null || true
  sleep 1

  ip link set "$IF_AP" up
  ip addr flush dev "$IF_AP" 2>/dev/null || true
  ip addr add "$AP_IP/24" dev "$IF_AP"

  cat > "$STATE_DIR/hostapd.conf" <<EOF
interface=$IF_AP
driver=nl80211
ssid=$AP_SSID
hwmode=11n
channel=$AP_CHANNEL
country_code=$AP_COUNTRY
wpa=2
psk=$AP_PASS
EOF

  # BeagleBone IP for bela.local: configured, else .1 of the gadget subnet
  local bip belabone; bip=$(cat "$BACKUP_DIR/beagle-ip" 2>/dev/null)
  belabone="${BEAGLE_IP:-${bip%.*}.1}"
  cat > "$STATE_DIR/dnsmasq.conf" <<EOF
port=0
bind-interfaces
# Access Point: DHCP + DNS for your PC / TB20plus
interface=$IF_AP
dhcp-range=${AP_DHCPRANGE},255.255.255.0,12h
dhcp-authoritative=yes
# DNS: the BELA IDE (so your PC on the AP can reach http://bela.local)
address=/$BEAGLE_NAME.local/$belabone
EOF

  hostapd "$STATE_DIR/hostapd.conf" -B 2>>"$LOG" || die "hostapd failed to start"
  dnsmasq -C "$STATE_DIR/dnsmasq.conf" --user=root 2>>"$LOG" || die "dnsmasq failed to start"
  log "AP up: SSID=$AP_SSID  IP=$AP_IP  (dnsmasq + hostapd running)"
}

# IP forwarding + NAT + firewall (AP -> robot LAN + BeagleBone gadget)
cmd_apply_forward(){
  sysctl -w net.ipv4.ip_forward=1 >/dev/null

  # BeagleBone gadget subnet = the /24 of the IP we captured on the SBC side
  local bip bnet; bip=$(cat "$BACKUP_DIR/beagle-ip" 2>/dev/null)
  [[ -n "$bip" ]] && bnet=$(subnet_of "$bip")

  # dedicated chains so disable removes exactly what we added
  iptables -N KUKA_NET_FWD 2>/dev/null || true
  iptables -N KUKA_NET_NAT 2>/dev/null || true
  iptables -F KUKA_NET_FWD; iptables -F KUKA_NET_NAT

  # AP (client side) may reach the robot LAN + BeagleBone
  iptables -A KUKA_NET_FWD -s "$AP_NET" -d "$KUKA_NET"   -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT
  [[ -n "$bnet" ]] && iptables -A KUKA_NET_FWD -s "$AP_NET" -d "$bnet" -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT
  # return traffic to the AP
  iptables -A KUKA_NET_FWD -d "$AP_NET" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  # NAT: masquerade the AP subnet
  iptables -t nat -A KUKA_NET_NAT -s "$AP_NET" -j MASQUERADE

  # open the service ports from the robot LAN + AP to the SBC
  local p
  for p in $RT_PORTS; do
    iptables -A KUKA_NET_FWD -s "$AP_NET" -p tcp --dport "$p" -j ACCEPT
  done

  # hook the chains (insert at top so they are evaluated first)
  iptables -C FORWARD -j KUKA_NET_FWD 2>/dev/null || iptables -I FORWARD -j KUKA_NET_FWD
  iptables -t nat -C POSTROUTING -j KUKA_NET_NAT 2>/dev/null || iptables -t nat -I POSTROUTING -j KUKA_NET_NAT
  log "forwarding + NAT + firewall applied (ports: $RT_PORTS; beagle net: ${bnet:-none})"
}

# ============================================================
#  disable — tear down and restore the standard config
# ============================================================
cmd_disable(){
  is_enabled || log "WARN: not marked enabled; cleaning up anyway."
  local mgr; mgr=$(cat "$MARKER" 2>/dev/null || echo "$(detect_manager)")

  # --- stop the AP daemons ---
  pkill -f "hostapd.*kuka-net" 2>/dev/null || true
  pkill -f "dnsmasq.*kuka-net" 2>/dev/null || true
  systemctl stop hostapd dnsmasq 2>/dev/null || true

  # --- remove firewall / NAT ---
  iptables -D FORWARD -j KUKA_NET_FWD 2>/dev/null || true
  iptables -t nat -D POSTROUTING -j KUKA_NET_NAT 2>/dev/null || true
  iptables -F KUKA_NET_FWD 2>/dev/null || true
  iptables -F KUKA_NET_NAT 2>/dev/null || true
  iptables -X KUKA_NET_FWD 2>/dev/null || true
  iptables -t nat -X KUKA_NET_NAT 2>/dev/null || true
  sysctl -w net.ipv4.ip_forward=0 >/dev/null

  # --- remove the static IPs we added ---
  ip addr del "$KUKA_IP/24" dev "$IF_KUKA" 2>/dev/null || true
  ip addr del "$RPI_PORT_IP/24" dev "$IF_RPI" 2>/dev/null || true
  local bif bip; bif=$(cat "$STATE_DIR/beagle-if" 2>/dev/null || echo "")
  bip=$(cat "$BACKUP_DIR/beagle-ip" 2>/dev/null)
  [[ -n "$bif" && -n "$bip" ]] && ip addr del "$bip" dev "$bif" 2>/dev/null || true
  ip addr del "$AP_IP/24" dev "$IF_AP" 2>/dev/null || true

  # --- restore the saved standard state ---
  if [[ -f "$BACKUP_DIR/ip-links" ]]; then
    ip a restore < "$BACKUP_DIR/ip-links" 2>/dev/null || true
  elif [[ -f "$BACKUP_DIR/ip-a" ]]; then
    log "WARN: only a raw `ip a` snapshot exists; re-applying manager config."
  fi
  [[ -f "$BACKUP_DIR/hosts" ]]       && cp -a "$BACKUP_DIR/hosts" /etc/hosts
  [[ -f "$BACKUP_DIR/resolv.conf" ]] && cp -a "$BACKUP_DIR/resolv.conf" /etc/resolv.conf

  # --- restart the config manager (re-establishes DHCP) ---
  case "$mgr" in
    nm)       systemctl start NetworkManager ;;
    networkd) systemctl start systemd-networkd ;;
    ifup)     dhclient "$IF_KUKA" 2>/dev/null || true ;;
  esac
  [[ "$DISABLE_TAILSCALE" == 1 ]] && systemctl start tailscaled 2>/dev/null || true

  rm -f "$MARKER"
  log "DISABLED. Standard config restored (manager: $mgr)."
}

# ============================================================
#  status / reset
# ============================================================
cmd_status(){
  echo "enabled : $(is_enabled && echo yes || echo no)"
  echo "manager : $(cat "$MARKER" 2>/dev/null || echo "$(detect_manager)")"
  echo
  echo "current addresses:"
  ip a | grep -E '^[0-9]+:|inet ' | sed 's/^/  /'
  echo
  echo "AP daemons:"
  pgrep -af "hostapd.*kuka-net" | sed 's/^/  hostapd: /' || echo "  hostapd: not running"
  pgrep -af "dnsmasq.*kuka-net" | sed 's/^/  dnsmasq: /' || echo "  dnsmasq: not running"
}

cmd_reset(){
  # force-restore the saved standard config regardless of state
  cmd_disable
  log "reset complete."
}

# ============================================================
#  main
# ============================================================
case "${1:-}" in
  discover) cmd_discover ;;
  install)  cmd_install ;;
  enable)   need_root enable; cmd_enable ;;
  disable)  need_root disable; cmd_disable ;;
  status)   cmd_status ;;
  reset)    need_root reset; cmd_reset ;;
  *) echo "usage: $0 {discover|install|enable|disable|status|reset}"; exit 2 ;;
esac
