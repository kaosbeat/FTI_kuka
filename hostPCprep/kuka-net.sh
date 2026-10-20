#!/usr/bin/env bash
#
# kuka-net.sh — Enable/disable the KUKA robot-lab network on the SBC and the RPi.
#
# Topology: the Kuka robot, the RPi 5 and the SBC all hang off ONE switch,
# and the switch carries the fixed robot LAN 192.168.10.0/24.
#
#   ROLE=sbc : the SBC (Debian trixie) — one ethernet port to the switch,
#              the second ethernet port left free, Intel AX210 wifi client,
#              BeagleBone/BELA USB gadget.
#   ROLE=rpi : the RPi 5 — one ethernet port to the switch + its own wifi client.
#
#   enable  -> builds the robot-lab network:
#       SBC  eth (enp15s0)  192.168.10.111/24   FIXED — the KRC (192.168.10.201) dials in
#       SBC  eth (enp24s0)  left free (normal DHCP, untouched)
#       RPi  eth (eth0)     192.168.10.113/24   FIXED
#       wifi (SBC wlp17s0 / RPi wlan0)  joins SSID satorigogo as a client
#       USB gadget (BELA)  kept as-is (SBC only, its own DHCP)
#     IP forwarding + NAT so wifi clients can reach the robot LAN, the RPi/SBC
#     (VNC, SSH) and http://bela.local. The wifi LAN is NATed, so it never
#     shares the Kuka's real-time segment.
#
#   disable -> tears all of that down and restores the standard DHCP config.
#
# Usage (as root):
#   sudo ./kuka-net.sh discover          # show the interfaces (numbered list)
#   sudo ./kuka-net.sh install           # one-time: install network-manager/iw/etc.
#   sudo ./kuka-net.sh configure         # interactive: pick interfaces/IPs/wifi, save config
#   sudo ./kuka-net.sh enable            # build the robot-lab network (TEMPORARY — until reboot)
#   sudo ./kuka-net.sh enable --persist  # build it AND make it survive reboots (boot service)
#   sudo ./kuka-net.sh disable           # revert to the standard config
#   sudo ./kuka-net.sh status            # show current state
#   sudo ./kuka-net.sh reset             # force-restore the saved standard config
#   sudo ./kuka-net.sh save [FILE]       # save the current robot-lab setup to a file
#   sudo ./kuka-net.sh restore [FILE]    # re-apply the robot-lab setup from a saved file
#
#   `enable` and `configure` are INTERACTIVE: they ask for the config variables,
#   showing a numbered list of interfaces (name + current IP + cable state), and
#   save the answer to /etc/kuka-net/config.env (later runs use it as defaults).
#   Before enabling, they OFFER a timestamped backup of the current network state:
#       /etc/kuka-net/backups/kuka-net-backup-<YYYYmmdd-HHMMSS>.tar.gz
#
#   DEFAULT (plain `enable`) is TEMPORARY: the robot-lab network only exists until
#   you reboot or run `disable`. No persistent config is written, so if you lose
#   connectivity and can't run `disable`, a reboot restores the standard DHCP setup.
#   `enable --persist` adds a boot service + sysctl so the network rebuilds itself
#   after every reboot; `disable` removes those artifacts.
#
set -uo pipefail

# ============================================================
#  CONFIG — defaults. `configure`/`enable` ask for these interactively
#  (numbered interface list with current IPs) and save the result to
#  /etc/kuka-net/config.env, which then takes precedence over these.
# ============================================================
# --- Role: which machine is this script running on? ("" = autodetect) ---
# sbc : the SBC — 2 ethernet ports, AX210 wifi, BeagleBone USB gadget
# rpi : the RPi 5 — 1 ethernet port to the switch + its own wifi
ROLE=""                      # "" = autodetect from the interfaces below

# --- SBC interfaces (run `discover` to see the real names) ---
SBC_SWITCH_IF="enp15s0"      # ethernet port -> switch (robot LAN)
SBC_FREE_IF="enp24s0"        # second ethernet port — left free (normal DHCP)
SBC_WIFI_IF="wlp17s0"        # Intel AX210 wifi -> client (joins WIFI_SSID)

# --- RPi interfaces ---
RPI_SWITCH_IF="eth0"         # ethernet port -> switch (robot LAN)
RPI_WIFI_IF="wlan0"          # RPi wifi -> client (joins WIFI_SSID)

# --- Kuka robot LAN (fixed, carried by the switch) ---
KUKA_IP="192.168.10.111" # SBC's FIXED IP — the KRC dials in from here
KUKA_NET="192.168.10.0/24"
KUKA_DEV="192.168.10.201" # KUKA robot (informational)
RPI_IP="192.168.10.113"  # RPi 5's FIXED IP on the switch

# --- BeagleBone / BELA (USB gadget, SBC only) ---
# The BeagleBone ALREADY works in your standard config: it runs its own USB
# gadget DHCP server. So we do NOT reconfigure its IP — we detect the gadget
# interface, keep it up, and NAT the wifi LAN to it so your PC can reach bela.local.
BEAGLE_IF=""                  # USB gadget iface on the SBC ("" = autodetect usb0)
BEAGLE_NAME="bela"            # IDE name -> http://bela.local
BEAGLE_IP=""                  # BeagleBone IP ("" = auto; else pin bela.local here)

# --- Wi-Fi client (SBC AX210 + RPi wifi — both join the same network) ---
WIFI_SSID="satorigogo"        # the home network both machines join
WIFI_PASS="wijsheid"

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
ENABLE_WIFI=1              # 1 = join the known wifi network (0 = skip it)
PERSIST_DEFAULT=0          # 0 = temporary (default, reverts on reboot); 1 = persist
                           # The CLI flag `--persist` overrides this for a single run.

# --- Role-derived variables (filled by set_role_vars; empty until detected) ---
SWITCH_IF=""   # the ethernet port to the switch for this role
SWITCH_IP=""   # the fixed IP on that port for this role
WIFI_IF=""     # the wifi client iface for this role

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
#  Interactive prompts (enable/configure only — never on a non-TTY)
# ============================================================
# ask "Label" "default" -> prints the prompt, reads one line; Enter = default.
ask(){
  local label="$1" def="$2" ans=""
  if [[ -t 0 ]]; then
    printf '  %s [%s]: ' "$label" "$def" > /dev/tty
    IFS= read -r ans
  fi
  if [[ -z "$ans" ]]; then echo "$def"; else echo "$ans"; fi
}

# ask_secret "Label" "default" -> same, but hidden input (wifi password).
ask_secret(){
  local label="$1" def="$2" ans=""
  if [[ -t 0 ]]; then
    printf '  %s [%s]: ' "$label" "$def" > /dev/tty
    IFS= read -rs ans
    echo > /dev/tty
  fi
  if [[ -z "$ans" ]]; then echo "$def"; else echo "$ans"; fi
}

# ask_yn "Label" "y|n" -> echoes 1/0 (Enter = default).
ask_yn(){
  local ans
  ans=$(ask "$1 (y/n)" "$2")
  [[ "$ans" =~ ^[Nn] ]] && echo 0 || echo 1
}

# --- Numbered interface list (name + current IP + type/cable state) ---
build_iface_list(){
  IFCACHE=()
  local if
  for if in $(ls /sys/class/net/ 2>/dev/null | sort); do
    case "$if" in lo|docker*|tailscale*|veth*|vm*|kvm*|virbr*|@*) continue ;; esac
    IFCACHE+=("$if")
  done
}

iface_note(){
  local if="$1" note="" is_wifi=0 c
  [[ -d "/sys/class/net/$if/wireless" ]] && { note="wifi"; is_wifi=1; }
  local dev
  dev=$(readlink -f "/sys/class/net/$if/device" 2>/dev/null || true)
  grep -qi 'usb' <<<"$dev" && note="USB ${note:+, $note}"
  [[ -z "$note" ]] && note="ethernet"
  if [[ "$is_wifi" == 0 ]]; then
    c=$(cat "/sys/class/net/$if/carrier" 2>/dev/null || echo 0)
    [[ "$c" == 1 ]] && note="$note (cable)" || note="$note (no cable)"
  fi
  echo "$note"
}

print_iface_list(){
  local n=1 if ip_
  printf '  %-4s %-14s %-18s %s\n' "#" "interface" "current IP" "type"
  for if in "${IFCACHE[@]}"; do
    ip_=$(iface_ip "$if")
    printf '  [%d] %-14s %-18s %s\n' "$n" "$if" "${ip_:-none}" "$(iface_note "$if")"
    n=$((n+1))
  done
}

iface_index(){
  local n=1 if
  for if in "${IFCACHE[@]}"; do
    [[ "$if" == "$1" ]] && { echo "$n"; return; }
    n=$((n+1))
  done
  echo 1
}

# ask_interface "Label" "default-index" -> echoes the chosen iface name.
# Accepts a number from the list, or an interface name typed directly.
ask_interface(){
  local label="$1" def="$2" choice i
  print_iface_list > /dev/tty
  while :; do
    choice=$(ask "$label (number)" "$def")
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#IFCACHE[@]} )); then
      echo "${IFCACHE[$((choice-1))]}"
      return
    fi
    for i in "${!IFCACHE[@]}"; do
      [[ "${IFCACHE[$i]}" == "$choice" ]] && { echo "$choice"; return; }
    done
    printf '  ? enter a number 1-%s (or an interface name)\n' "${#IFCACHE[@]}" > /dev/tty
  done
}

# ============================================================
#  Config persistence — /etc/kuka-net/config.env
# ============================================================
load_config(){
  [[ -f "$STATE_DIR/config.env" ]] && . "$STATE_DIR/config.env"
}

write_config(){
  mkdir -p "$STATE_DIR"
  cat > "$STATE_DIR/config.env" <<EOF
# kuka-net config — written $(date '+%Y-%m-%d %H:%M:%S')
ROLE="$ROLE"
SBC_SWITCH_IF="$SBC_SWITCH_IF"
SBC_FREE_IF="$SBC_FREE_IF"
SBC_WIFI_IF="$SBC_WIFI_IF"
RPI_SWITCH_IF="$RPI_SWITCH_IF"
RPI_WIFI_IF="$RPI_WIFI_IF"
KUKA_IP="$KUKA_IP"
RPI_IP="$RPI_IP"
BEAGLE_IF="$BEAGLE_IF"
BEAGLE_NAME="$BEAGLE_NAME"
BEAGLE_IP="$BEAGLE_IP"
WIFI_SSID="$WIFI_SSID"
WIFI_PASS="$WIFI_PASS"
RT_PORTS="$RT_PORTS"
DISABLE_TAILSCALE="$DISABLE_TAILSCALE"
ENABLE_WIFI="$ENABLE_WIFI"
EOF
  log "config saved to $STATE_DIR/config.env"
}

# ============================================================
#  Timestamped backup of the current network state
# ============================================================
make_backup(){
  local ts out
  ts=$(date +%Y%m%d-%H%M%S)
  out="$STATE_DIR/backups/kuka-net-backup-$ts.tar.gz"
  mkdir -p "$STATE_DIR/backups"
  cp -a "$0" "$BACKUP_DIR/kuka-net.sh"
  [[ -f "$STATE_DIR/config.env" ]] && cp -a "$STATE_DIR/config.env" "$BACKUP_DIR/config.env"
  command -v iptables-save >/dev/null 2>&1 && iptables-save > "$BACKUP_DIR/iptables-save" 2>/dev/null || true
  tar czf "$out" -C "$STATE_DIR" backup
  echo "$out"
}

# Offer the timestamped backup before anything is changed (Enter = yes).
offer_backup(){
  local ans out
  ans=$(ask_yn "Save a backup of the current network config? (timestamped tarball)" "y")
  if [[ "$ans" == 1 ]]; then
    out=$(make_backup)
    echo "  backup saved: $out"
    log "timestamped backup: $out"
  else
    echo "  (no timestamped backup)"
  fi
}

# ============================================================
#  interactive_configure — ask for the config variables (TTY only)
# ============================================================
interactive_configure(){
  mkdir -p "$STATE_DIR"
  build_iface_list
  [[ ${#IFCACHE[@]} -gt 0 ]] || die "no physical interfaces found"

  echo
  echo "=== kuka-net interactive config ==="
  echo "(press Enter to accept the [default])"

  # --- Role ---
  local detected auto role_
  detected=$(detect_role)
  if [[ "$detected" == "rpi" ]]; then auto=2; else auto=1; fi
  echo
  echo "  Which machine are you configuring? (auto-detected: ${detected:-unknown})"
  echo "    [1] SBC   — 2 ethernet ports + AX210 wifi + BeagleBone USB gadget"
  echo "    [2] RPi 5 — 1 ethernet port to the switch + its own wifi"
  while :; do
    role_=$(ask "role (1/2)" "$auto")
    case "$role_" in 1|2) break ;; esac
    printf '  ? enter 1 or 2\n' > /dev/tty
  done
  if [[ "$role_" == 1 ]]; then ROLE=sbc; else ROLE=rpi; fi

  # --- Interfaces for the chosen role ---
  if [[ "$ROLE" == "sbc" ]]; then
    SBC_SWITCH_IF=$(ask_interface "SBC ethernet port cabled to the SWITCH (robot LAN)" "$(iface_index "$SBC_SWITCH_IF")")
    while [[ "$SBC_FREE_IF" == "$SBC_SWITCH_IF" ]]; do
      SBC_FREE_IF=$(ask_interface "SBC second ethernet port (left free, normal DHCP)" "$(iface_index "$SBC_FREE_IF")")
    done
    SBC_WIFI_IF=$(ask_interface "SBC wifi (AX210 — joins the home network)" "$(iface_index "$SBC_WIFI_IF")")
    local bg; bg=$(detect_beagle)
    BEAGLE_IF=$(ask_interface "BeagleBone USB gadget (bela — already working)" "$(iface_index "${BEAGLE_IF:-$bg}")")
  else
    RPI_SWITCH_IF=$(ask_interface "RPi ethernet port cabled to the SWITCH (robot LAN)" "$(iface_index "$RPI_SWITCH_IF")")
    RPI_WIFI_IF=$(ask_interface "RPi wifi (joins the home network)" "$(iface_index "$RPI_WIFI_IF")")
  fi

  # --- Fixed IPs on the robot LAN ---
  KUKA_IP=$(ask "SBC fixed IP on the robot LAN (the KRC dials in)" "$KUKA_IP")
  RPI_IP=$(ask "RPi 5 fixed IP on the robot LAN" "$RPI_IP")
  if [[ "$ROLE" == "sbc" ]]; then
    BEAGLE_IP=$(ask "BeagleBone IP (Enter = auto)" "$BEAGLE_IP")
  fi

  # --- Wifi client + options ---
  ENABLE_WIFI=$(ask_yn "Join the home wifi (client mode) during enable?" "y")
  if [[ "$ENABLE_WIFI" == 1 ]]; then
    WIFI_SSID=$(ask "Wifi SSID to join" "$WIFI_SSID")
    WIFI_PASS=$(ask_secret "Wifi password" "$WIFI_PASS")
  fi
  DISABLE_TAILSCALE=$(ask_yn "Stop Tailscale during enable (routing isolation)?" "y")

  echo
  echo "  Config:"
  echo "    role          : $ROLE"
  echo "    switch port   : $([[ "$ROLE" == "sbc" ]] && echo "$SBC_SWITCH_IF" || echo "$RPI_SWITCH_IF")"
  echo "    wifi          : $([[ "$ROLE" == "sbc" ]] && echo "$SBC_WIFI_IF" || echo "$RPI_WIFI_IF")"
  echo "    SBC IP        : $KUKA_IP   RPi IP: $RPI_IP"
  echo "    wifi SSID     : $WIFI_SSID"
}

# Which machine are we on? Explicit ROLE wins; else infer from the ifaces.
detect_role(){
  [[ -n "$ROLE" ]] && { echo "$ROLE"; return; }
  if [[ -d "/sys/class/net/$SBC_SWITCH_IF" && -d "/sys/class/net/$SBC_FREE_IF" ]]; then
    echo sbc; return
  fi
  if [[ -d "/sys/class/net/$RPI_SWITCH_IF" && -d "/sys/class/net/$RPI_WIFI_IF" ]]; then
    echo rpi; return
  fi
  # fallback: only the SBC carries the BeagleBone USB gadget
  [[ -n "$(detect_beagle)" ]] && { echo sbc; return; }
  echo ""
}

# Set SWITCH_IF / SWITCH_IP / WIFI_IF from the detected role. Returns 1 if unknown.
set_role_vars(){
  local r
  r=$(detect_role)
  [[ -n "$r" ]] || return 1
  case "$r" in
    sbc) SWITCH_IF="$SBC_SWITCH_IF"; SWITCH_IP="$KUKA_IP"; WIFI_IF="$SBC_WIFI_IF" ;;
    rpi) SWITCH_IF="$RPI_SWITCH_IF"; SWITCH_IP="$RPI_IP";   WIFI_IF="$RPI_WIFI_IF" ;;
  esac
  return 0
}

# Pick the saved wifi connection to activate: the one matching WIFI_SSID,
# else the first saved (known) wifi connection. Empty = none found.
pick_wifi_conn(){
  local c
  if [[ -n "$WIFI_SSID" ]]; then
    c=$(nmcli -t -f NAME,TYPE connection show 2>/dev/null | awk -F: -v s="$WIFI_SSID" '$2=="wifi" && $1==s {print $1; exit}')
    [[ -n "$c" ]] && { echo "$c"; return; }
  fi
  nmcli -t -f NAME,TYPE connection show 2>/dev/null | awk -F: '$2=="wifi" {print $1; exit}'
}

# ============================================================
#  discover — print the interfaces so you can fill in the CONFIG
# ============================================================
cmd_discover(){
  echo "=== Network interfaces ==="
  build_iface_list
  print_iface_list
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
  echo "=== Detected role ==="
  echo "  $(detect_role || echo 'unknown — set ROLE=sbc or ROLE=rpi in the CONFIG')"
  echo
  echo "HINT: put the interface names above into the CONFIG section of this script."
  echo "      SBC : SBC_SWITCH_IF = the port cabled to the switch (SBC_FREE_IF stays free)."
  echo "      RPi : RPI_SWITCH_IF = the port cabled to the switch; RPI_WIFI_IF = its wifi."
}

# ============================================================
#  install — one-time package setup
# ============================================================
cmd_install(){
  apt-get update
  apt-get install -y network-manager iw iproute2 iptables avahi-daemon
  mkdir -p "$STATE_DIR"
  log "installed: network-manager iw iproute2 iptables avahi-daemon"
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
  echo "role=$(detect_role) switch=${SBC_SWITCH_IF} free=${SBC_FREE_IF} wifi_sbc=${SBC_WIFI_IF} wifi_rpi=${RPI_WIFI_IF} beagle=${BEAGLE_IF:-auto}" > "$BACKUP_DIR/ifaces"
  # capture the BeagleBone gadget iface + its current IP (so we can keep it up)
  local bif; bif="${BEAGLE_IF:-$(detect_beagle)}"
  echo "$bif" > "$BACKUP_DIR/beagle-if"
  echo "$(iface_ip "$bif" 2>/dev/null)" > "$BACKUP_DIR/beagle-ip"
  log "snapshot saved to $BACKUP_DIR (manager: $(detect_manager))"
}

# ============================================================
#  apply_runtime — build the robot-lab network (runtime only, no snapshot)
#  Called by `enable` and by the `apply` boot-service target (--persist).
# ============================================================
apply_runtime(){
  set_role_vars || die "cannot detect the role — set ROLE=sbc or ROLE=rpi in the CONFIG"
  local role; role=$(detect_role)
  log "role: $role (switch=$SWITCH_IF $SWITCH_IP/24, wifi=$WIFI_IF)"

  # --- the wifi client needs NetworkManager; otherwise stop the config manager ---
  local mgr use_nm_wifi
  mgr=$(detect_manager)
  use_nm_wifi=0
  [[ "$ENABLE_WIFI" == 1 && "$mgr" == "nm" ]] && use_nm_wifi=1
  if [[ "$use_nm_wifi" == 1 ]]; then
    # keep NM alive (it owns the wifi client); take the static switch port away
    nmcli device set "$SWITCH_IF" managed no 2>/dev/null || true
    log "NM kept for the wifi client; $SWITCH_IF unmanaged"
  else
    case "$mgr" in
      nm)       systemctl stop NetworkManager ;;
      networkd) systemctl stop systemd-networkd ;;
      ifup)     : ;;
    esac
    log "stopped manager: $mgr"
  fi
  [[ "$DISABLE_TAILSCALE" == 1 ]] && systemctl stop tailscaled 2>/dev/null || true

  # --- switch port (fixed, no default route) ---
  ip link set "$SWITCH_IF" up
  ip addr flush dev "$SWITCH_IF" 2>/dev/null || true
  ip addr add "$SWITCH_IP/24" dev "$SWITCH_IF"
  log "switch port $SWITCH_IF -> $SWITCH_IP/24"

  # --- the SBC's second ethernet port stays free (normal DHCP, untouched) ---
  if [[ "$role" == "sbc" ]]; then
    log "free port   $SBC_FREE_IF left untouched (normal config)"
  fi

  # --- BeagleBone USB gadget (SBC only; keep the existing gadget link alive) ---
  local bif bip belabone
  bif=""; bip=""
  if [[ "$role" == "sbc" ]]; then
    bif=$(cat "$BACKUP_DIR/beagle-if" 2>/dev/null || echo "${BEAGLE_IF:-$(detect_beagle)}")
    bip=$(cat "$BACKUP_DIR/beagle-ip" 2>/dev/null)
    if [[ -n "$bif" && -n "$bip" ]]; then
      ip link set "$bif" up
      ip addr del "$bip" dev "$bif" 2>/dev/null || true
      ip addr add "$bip" dev "$bif"
      echo "$bif" > "$STATE_DIR/beagle-if"
      log "BeagleBone $bif -> $bip (gadget link kept, subnet $(subnet_of "$bip"))"
    else
      log "WARN: no USB gadget IP captured for the BeagleBone; bela.local may be unreachable from the wifi clients"
    fi
  fi

  # --- Wi-Fi client (joins WIFI_SSID) ---
  if [[ "$ENABLE_WIFI" == 1 ]]; then
    if [[ "$use_nm_wifi" == 1 ]]; then
      cmd_apply_wifi
    else
      log "WARN: wifi client needs NetworkManager (found: $mgr); wifi skipped"
    fi
  fi

  # --- routing + NAT + firewall ---
  cmd_apply_forward

  # --- DNS for the SBC itself (bela.local) ---
  if [[ "$role" == "sbc" ]]; then
    belabone="${BEAGLE_IP:-${bip%.*}.1}"
    grep -q "bela.local" /etc/hosts || echo "$belabone  $BEAGLE_NAME.local" >> /etc/hosts
  fi

  # --- mark enabled ---
  echo "$mgr" > "$MARKER"
  echo "$role" > "$STATE_DIR/role"
  local wip; wip=$(iface_ip "$WIFI_IF" 2>/dev/null || echo none)
  log "APPLIED (runtime). role=$role switch=$SWITCH_IP  RPi=$RPI_IP  wifi=$wip on $WIFI_IF"
  print_hints
}

# Human-facing hints: wifi client state, robot-LAN layout, how to reach BELA.
print_hints(){
  local role belabone bip bdisp wip wconn
  set_role_vars || { echo "(role unknown — set ROLE=sbc or ROLE=rpi in the CONFIG)"; return; }
  role=$(detect_role)
  wip=$(iface_ip "$WIFI_IF" 2>/dev/null || echo none)
  wconn=$(pick_wifi_conn 2>/dev/null); wconn=${wconn:-none}
  echo
  echo "================================================================"
  echo "  ROBOT-LAB NETWORK (role: ${role:-?}) — how to use it"
  echo "================================================================"
  echo "  [1] WIFI CLIENT  (this machine joins '$WIFI_SSID' on $WIFI_IF)"
  echo "        Saved connection : $wconn   (WIFI_SSID is pinned in the CONFIG)"
  echo "        IP on it         : $wip"
  echo "        Your PC / TB20plus join the SAME network and reach this"
  echo "        machine at that IP (VNC, SSH, the robot-lab services)."
  echo
  echo "  [2] ROBOT LAN (the switch carries $KUKA_NET)"
  echo "        SBC  : $KUKA_IP   (the KRC dials in from here)"
  echo "        RPi  : $RPI_IP    (this script sets it on the RPi too)"
  echo "        KRC  : $KUKA_DEV  (the robot)"
  echo "        Verify:  ping -c1 $RPI_IP"
  if [[ "$role" == "sbc" ]]; then
    echo
    echo "  [3] BELA  (from your PC, while on the same wifi network)"
    bip=$(cat "$BACKUP_DIR/beagle-ip" 2>/dev/null)
    belabone="${BEAGLE_IP:-${bip%.*}.1}"
    bdisp="$belabone"
    [[ "$belabone" == .* ]] && bdisp="(auto — from the BeagleBone USB-gadget link)"
    echo "        Open  http://$bdisp   (the IDE)"
    echo "        bela.local resolves on the SBC itself; from your PC use"
    echo "        the BeagleBone IP directly. The SBC NATs the wifi LAN"
    echo "        to the BeagleBone USB-gadget link."
  fi
  echo "================================================================"
}

# ============================================================
#  configure — interactive: ask for the config variables and save them
#  (does NOT build the network; just writes /etc/kuka-net/config.env)
# ============================================================
cmd_configure(){
  load_config
  if [[ -t 0 ]]; then
    interactive_configure
    write_config
  else
    die "configure needs a TTY: run it from a terminal (not piped)"
  fi
}

# ============================================================
#  enable — snapshot the standard state, then build the network
# ============================================================
cmd_enable(){
  local persist="${1:-$PERSIST_DEFAULT}"
  load_config   # saved config.env becomes the defaults (TTY) / the config (non-TTY)
  if [[ -t 0 ]]; then
    interactive_configure
    write_config
  else
    [[ -f "$STATE_DIR/config.env" ]] || die "no saved config and no TTY: run 'sudo $0 configure' (or 'enable') from a terminal first"
    echo "  (using saved config: $STATE_DIR/config.env)"
  fi
  is_enabled && { log "already enabled (marker present); re-applying."; }
  mkdir -p "$STATE_DIR"
  snapshot
  offer_backup
  apply_runtime
  if [[ "$persist" == 1 ]]; then
    make_persistent
  else
    log "NOTE: TEMPORARY — this network reverts on reboot (or 'disable'). Add --persist to keep it."
  fi
}

# Make the runtime setup survive reboots: boot service + sysctl.
make_persistent(){
  local script; script="$(readlink -f "$0")"
  echo "$script" > "$STATE_DIR/script-path"

  # persist IP forwarding across reboots
  echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/kuka-net.conf

  # boot service that re-applies the runtime config (no snapshot, no clobber)
  cat > /etc/systemd/system/kuka-net.service <<EOF
[Unit]
Description=KUKA robot-lab network (persistent)
Wants=network-online.target
After=network-online.target NetworkManager.service systemd-networkd.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart="$script" apply

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable kuka-net
  echo "1" > "$STATE_DIR/persist"
  log "PERSISTENT: boot service 'kuka-net' enabled + ip_forward persisted (survives reboot)."
}

# Remove the persistence artifacts (boot service + sysctl).
remove_persistent(){
  if [[ -f /etc/systemd/system/kuka-net.service ]]; then
    systemctl disable kuka-net 2>/dev/null || true
    systemctl stop kuka-net 2>/dev/null || true
    rm -f /etc/systemd/system/kuka-net.service
    systemctl daemon-reload
  fi
  rm -f /etc/sysctl.d/kuka-net.conf
  rm -f "$STATE_DIR/persist"
  log "persistent artifacts removed."
}

# Wi-Fi as a client: join WIFI_SSID via NetworkManager (SBC AX210 or RPi wifi).
cmd_apply_wifi(){
  local conn ip
  conn=$(pick_wifi_conn)
  if [[ -z "$conn" ]]; then
    log "WARN: no saved wifi connection found for $WIFI_IF (set WIFI_SSID or save the network first); wifi skipped"
    return
  fi
  log "wifi: activating saved connection '$conn' on $WIFI_IF"
  nmcli connection up "$conn" ifname "$WIFI_IF" 2>>"$LOG" || true
  # fall back to an explicit SSID/password if the saved activation left no IP
  if [[ -z "$(iface_ip "$WIFI_IF")" && -n "$WIFI_SSID" ]]; then
    nmcli device wifi connect "$WIFI_SSID" password "$WIFI_PASS" ifname "$WIFI_IF" 2>>"$LOG" || true
  fi
  # wait for a DHCP lease (up to 30s)
  for _ in $(seq 1 30); do
    ip=$(iface_ip "$WIFI_IF")
    [[ -n "$ip" ]] && break
    sleep 1
  done
  if [[ -n "$ip" ]]; then
    log "wifi up: $WIFI_IF -> $ip (connection: $conn)"
  else
    log "WARN: $WIFI_IF has no IP after 30s (connection: $conn); check the network"
  fi
}

# IP forwarding + NAT + firewall (wifi client LAN -> robot LAN + BeagleBone gadget)
cmd_apply_forward(){
  sysctl -w net.ipv4.ip_forward=1 >/dev/null

  # BeagleBone gadget subnet = the /24 of the IP we captured on the SBC side
  local bip bnet; bip=$(cat "$BACKUP_DIR/beagle-ip" 2>/dev/null)
  [[ -n "$bip" ]] && bnet=$(subnet_of "$bip")

  # wifi client subnet = the /24 of the DHCP lease on the wifi iface (dynamic)
  local wnet; wnet=$(subnet_of "$(iface_ip "$WIFI_IF" 2>/dev/null)")
  [[ "$wnet" == "$KUKA_NET" ]] && log "WARN: wifi subnet $wnet collides with the Kuka LAN $KUKA_NET"

  # dedicated chains so disable removes exactly what we added
  iptables -N KUKA_NET_FWD 2>/dev/null || true
  iptables -N KUKA_NET_NAT 2>/dev/null || true
  iptables -F KUKA_NET_FWD; iptables -F KUKA_NET_NAT

  # the wifi LAN (client side) may reach the robot LAN + BeagleBone
  if [[ -n "$wnet" ]]; then
    iptables -A KUKA_NET_FWD -s "$wnet" -d "$KUKA_NET"   -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT
    [[ -n "$bnet" ]] && iptables -A KUKA_NET_FWD -s "$wnet" -d "$bnet" -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT
    # return traffic to the wifi LAN
    iptables -A KUKA_NET_FWD -d "$wnet" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    # NAT: masquerade the wifi LAN subnet
    iptables -t nat -A KUKA_NET_NAT -s "$wnet" -j MASQUERADE
    # open the service ports from the robot LAN + wifi LAN to the SBC
    local p
    for p in $RT_PORTS; do
      iptables -A KUKA_NET_FWD -s "$wnet" -p tcp --dport "$p" -j ACCEPT
    done
  else
    log "WARN: no IP on $WIFI_IF yet; client-side NAT skipped (wifi clients cannot reach the robot LAN)"
  fi

  # hook the chains (insert at top so they are evaluated first)
  iptables -C FORWARD -j KUKA_NET_FWD 2>/dev/null || iptables -I FORWARD -j KUKA_NET_FWD
  iptables -t nat -C POSTROUTING -j KUKA_NET_NAT 2>/dev/null || iptables -t nat -I POSTROUTING -j KUKA_NET_NAT
  log "forwarding + NAT + firewall applied (ports: $RT_PORTS; wifi net: ${wnet:-none}; beagle net: ${bnet:-none})"
}

# ============================================================
#  disable — tear down and restore the standard config
# ============================================================
cmd_disable(){
  is_enabled || log "WARN: not marked enabled; cleaning up anyway."
  local mgr; mgr=$(cat "$MARKER" 2>/dev/null || echo "$(detect_manager)")
  remove_persistent
  set_role_vars || log "WARN: role not detected; interface restore skipped."

  # --- re-manage the switch port we took away from NetworkManager ---
  if [[ "$(detect_manager)" == "nm" ]]; then
    nmcli device set "$SWITCH_IF" managed yes 2>/dev/null || true
  fi

  # --- stop any legacy AP daemons (from the old access-point setup) ---
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

  # --- remove the static IP we added ---
  ip addr del "$SWITCH_IP/24" dev "$SWITCH_IF" 2>/dev/null || true
  local bif bip; bif=$(cat "$STATE_DIR/beagle-if" 2>/dev/null || echo "")
  bip=$(cat "$BACKUP_DIR/beagle-ip" 2>/dev/null)
  [[ -n "$bif" && -n "$bip" ]] && ip addr del "$bip" dev "$bif" 2>/dev/null || true
  # the wifi IP is the normal NM-managed DHCP lease; NM (re)starts below

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
    ifup)     dhclient "$SWITCH_IF" 2>/dev/null || true ;;
  esac
  [[ "$DISABLE_TAILSCALE" == 1 ]] && systemctl start tailscaled 2>/dev/null || true

  rm -f "$MARKER"
  log "DISABLED. Standard config restored (manager: $mgr)."
}

# ============================================================
#  status / reset
# ============================================================
cmd_status(){
  local role; role=$(detect_role)
  set_role_vars || { echo "role: unknown (set ROLE=sbc or ROLE=rpi in the CONFIG)"; }
  echo "role             : ${role:-?}"
  echo "enabled (marker) : $(is_enabled && echo yes || echo no)"
  echo "persist          : $([[ -f "$STATE_DIR/persist" ]] && echo yes || echo no)"
  echo "runtime active   : $(ip addr show "$SWITCH_IF" 2>/dev/null | grep -q "$SWITCH_IP" && echo yes || echo no)"
  echo "manager          : $(cat "$MARKER" 2>/dev/null || echo "$(detect_manager)")"
  echo
  echo "current addresses:"
  ip a | grep -E '^[0-9]+:|inet ' | sed 's/^/  /'
  echo
  echo "wifi client ($WIFI_IF):"
  nmcli -t -f DEVICE,STATE,CONNECTION device 2>/dev/null | awk -F: -v if_="$WIFI_IF" '$1==if_ {print "  " $1": state=" $2 " connection=" ($3=="" ? "none" : $3)}' || echo "  (nmcli unavailable)"
  echo "  iface IP: $(iface_ip "$WIFI_IF" 2>/dev/null || echo none)"
  echo
  print_hints
}

cmd_reset(){
  # force-restore the saved standard config regardless of state
  cmd_disable
  log "reset complete."
}

# ============================================================
#  save / restore — snapshot the robot-lab setup to a file
# ============================================================
cmd_save(){
  local out="${1:-$STATE_DIR/saved-setup.conf}"
  local dir; dir="$(dirname "$out")"
  mkdir -p "$dir" 2>/dev/null || true
  local v
  {
    echo "# kuka-net saved robot-lab setup — $(date)"
    echo "# Re-apply with:  sudo ./kuka-net.sh restore $out"
    echo
    for v in ROLE SBC_SWITCH_IF SBC_FREE_IF SBC_WIFI_IF RPI_SWITCH_IF RPI_WIFI_IF KUKA_IP KUKA_NET KUKA_DEV RPI_IP BEAGLE_IF BEAGLE_NAME BEAGLE_IP WIFI_SSID WIFI_PASS RT_PORTS DISABLE_TAILSCALE ENABLE_WIFI; do
      printf '%s=%q\n' "$v" "${!v}"
    done
    echo
    echo "# --- runtime state dump (reference only) ---"
    echo "# --- ip a ---";           ip a 2>/dev/null               | sed 's/^/# /'
    echo "# --- ip route ---";       ip route 2>/dev/null           | sed 's/^/# /'
    echo "# --- KUKA_NET_FWD ---";   iptables -S KUKA_NET_FWD 2>/dev/null | sed 's/^/# /'
    echo "# --- wifi client ---";    nmcli -t -f DEVICE,STATE,CONNECTION device 2>/dev/null | sed 's/^/# /' || echo "# (none)"
    echo "# --- /etc/hosts (bela) ---"; grep -i 'bela' /etc/hosts 2>/dev/null | sed 's/^/# /' || echo "# (none)"
  } > "$out"
  echo "saved current robot-lab setup to: $out"
}

cmd_restore(){
  local f="${1:-$STATE_DIR/saved-setup.conf}"
  [[ -f "$f" ]] || die "save file not found: $f  (run 'save' first)"
  local line
  while IFS= read -r line; do
    [[ "$line" =~ ^[A-Z_][A-Z0-9_]+= ]] && eval "$line"
  done < "$f"
  mkdir -p "$STATE_DIR"
  apply_runtime
  log "RESTORED robot-lab setup from $f"
}

# ============================================================
#  main
# ============================================================
main(){
  local persist="$PERSIST_DEFAULT"
  local cmd="" a
  local -a rest=()
  for a in "$@"; do
    case "$a" in
      --persist) persist=1 ;;
      -*) die "unknown option: $a" ;;
      *) rest+=("$a") ;;
    esac
  done
  cmd="${rest[0]:-}"
  case "$cmd" in
    discover) cmd_discover ;;
    install)  cmd_install ;;
    configure) need_root configure; cmd_configure ;;
    enable)   need_root enable;   cmd_enable "$persist" ;;
    apply)    need_root apply;    apply_runtime ;;   # internal: boot-service target
    disable)  need_root disable;  cmd_disable ;;
    status)   cmd_status ;;
    hints)    print_hints ;;
    reset)    need_root reset;    cmd_reset ;;
    save)     need_root save;     cmd_save "${rest[1]:-}" ;;
    restore)  need_root restore;  cmd_restore "${rest[1]:-}" ;;
    *) echo "usage: $0 {discover|install|configure|enable [--persist]|disable|status|hints|reset|save [FILE]|restore [FILE]}"; exit 2 ;;
  esac
}
main "$@"
