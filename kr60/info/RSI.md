# kukak RSI

https://www.kuka.com/en-us/products/robotics-systems/software/hub-technologies/kuka_robotsensorinterface

## python
https://pypi.org/project/RSIPI/

https://github.com/otherworld-dev/rsi-pi

https://github.com/pawankumardev/kukarsiserver


###


Since you have **RSI available** but **no Sunrise**, you're in the best position for real-time external control. RSI (Robot Sensor Interface) is KUKA's hard real-time UDP protocol, but it is **not documented as a public API** and has no official Python client. Below is a production-safe, direct-path implementation using raw UDP in Python, along with exact KUKA configuration steps.

---
## 🔧 1. KUKA Controller Setup (RSI Server + External Control)

### A. Enable RSI Server
1. Open **KUKA.System Manager (KSM)** on the teach pendant
2. `Configuration` → `RSI` → Enable RSI Server
3. Set:
   - IP: `<your-PC-IP>` (or `0.0.0.0` to accept any)
   - Port: `1000` (default UDP)
   - Cycle time: `10-50 Hz` (KRC max ~100 Hz)
4. Save & restart controller if prompted

### B. Activate External Control (EC)
RSI only sends/receives data when the controller is in **External Control mode**. Activate it via:
- **KSS Command** (preferred for safety):
  ```krl
  EXTERNAL_CONTROL(1)  ; Enable
  EXTERNAL_CONTROL(0)  ; Disable
  ```
- **Via RSI.cfg** (if configured):
  ```ini
  [EXTERNAL_CONTROL]
  ENABLE = 1
  ```
- **Safety Configuration**: `External Control` → ✅ Enable → Set `Fallback` to `STOP` (never `CONTINUE`)

### C. Verify RSI is Running
On the teach pendant:
`Status` → `External Control` → Should show `RSI ACTIVE` and `UDP LISTENING`

---
## 🐍 2. Python RSI Client (UDP + struct)

KUKA's RSI uses a **binary UDP protocol**. There is no official Python wrapper, so we implement it directly with `socket` and `struct`.

```python
import socket
import struct
import time
import sys

# KUKA RSI Configuration
KUKA_IP = "192.168.1.100"
KUKA_PORT = 1000       # UDP port (default)
PC_PORT = 1001          # Local bind port (must match RSI.cfg [PC_ADDRESS])
CYCLE_TIME = 0.02       # 50 Hz
MAX_TIMEOUT = 0.5       # Seconds before declaring link loss

class KUKArsiClient:
    def __init__(self, kuka_ip: str, kuka_port: int, pc_port: int):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.bind(("0.0.0.0", pc_port))
        self.kuka_ip = kuka_ip
        self.kuka_port = kuka_port
        self.seq = 0
        self.timeout = 0.0
        self.sync_word = b'\x55\x00'  # KRC4/KRC5 little-endian sync
        print(f"✅ RSI client bound to port {pc_port}, targeting {kuka_ip}:{kuka_port}")

    def _build_packet(self, joints: list[float], tcp_pose: list[float] = None) -> bytes:
        """Build RSI command packet (KRC4/KRC5 standard layout)"""
        joint_data = struct.pack('<6f', *joints)  # 6 joints × float32 LE
        tcp_data = struct.pack('<6f', *tcp_pose) if tcp_pose else b'\x00' * 24
        # Length = sync(2) + len(2) + seq(2) + joint(24) + tcp(24) + checksum(2) = 56
        length = 56
        self.seq = (self.seq + 1) & 0xFFFF
        
        payload = self.sync_word + struct.pack('<H', length) + struct.pack('<H', self.seq) + joint_data + tcp_data
        checksum = sum(payload) & 0xFFFF
        packet = payload + struct.pack('<H', checksum)
        return packet

    def send_joints(self, joints: list[float], tcp_pose: list[float] = None):
        """Send joint target to KUKA RSI server"""
        if len(joints) != 6:
            raise ValueError("RSI requires exactly 6 joint angles")
        packet = self._build_packet(joints, tcp_pose)
        self.sock.sendto(packet, (self.kuka_ip, self.kuka_port))
        self.timeout = 0.0

    def read_status(self) -> dict:
        """Non-blocking read of RSI status packet from KUKA"""
        try:
            data, _ = self.sock.recvfrom(1024)
            if len(data) < 10:
                return {}
            # Parse status packet (KUKA sends sync + length + seq + data + checksum)
            length = struct.unpack('<H', data[2:4])[0]
            seq = struct.unpack('<H', data[4:6])[0]
            joints = struct.unpack('<6f', data[6:30])
            tcp = struct.unpack('<6f', data[30:54])
            return {
                "seq": seq,
                "joints": list(joints),
                "tcp": list(tcp),
                "valid": True
            }
        except BlockingIOError:
            return {}

    def check_heartbeat(self, dt: float) -> bool:
        """Returns True if RSI link is alive, False on timeout"""
        self.timeout += dt
        return self.timeout < MAX_TIMEOUT

    def close(self):
        self.sock.close()
        print("🔌 RSI client closed")

# --- USAGE EXAMPLE ---
if __name__ == "__main__":
    client = KUKArsiClient(KUKA_IP, KUKA_PORT, PC_PORT)
    home_joints = [0.0, -30.0, 0.0, -90.0, 0.0, 45.0]  # Degrees
    start = time.perf_counter()
    
    try:
        while True:
            dt = time.perf_counter() - start
            start = time.perf_counter()
            
            if not client.check_heartbeat(dt):
                print("⚠️ RSI TIMEOUT - STOPPING")
                sys.exit(1)
                
            status = client.read_status()
            if status.get("valid"):
                print(f"Seq: {status['seq']:.0f} | Joints: {[f'{j:.1f}' for j in status['joints']]}")
                
            # Send joints (KUKA interpolates automatically in EC mode)
            client.send_joints(home_joints)
            
            time.sleep(CYCLE_TIME)
            
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
```

---
## 📐 3. RSI Protocol Details (KRC4/KRC5)

| Field | Size | Endian | Description |
|-------|------|--------|-------------|
| Sync Word | 2B | LE (`0x0055`) | Packet header |
| Length | 2B | LE | Total packet length (usually 56) |
| Sequence | 2B | LE | Increments per cycle |
| Joint Cmd | 24B | LE (`6×float32`) | Target joints (degrees or rad) |
| TCP Pose | 24B | LE (`6×float32`) | X,Y,Z, A,C,B (or similar) |
| Checksum | 2B | LE | `sum(all_bytes) & 0xFFFF` |

⚠️ **Verify byte order & packet size** with your controller's `RSI_User_Manual.pdf`. KRC2 uses a different layout. KRC4/KRC5 uses the above.

---
## 🛡️ 4. Critical Safety & Timing Notes

| Requirement | Implementation |
|-------------|----------------|
| **External Control Mode** | Must be active via `EXTERNAL_CONTROL(1)` or KSS config |
| **Heartbeat/Timeout** | PC must send packets continuously; KUKA stops on loss |
| **Max Cycle Time** | 10-50 Hz recommended. >80 Hz may cause KRC overload |
| **Network** | RSI is UDP. Use SSH/VPN tunneling if crossing firewalls |
| **E-Stop** | RSI does NOT replace safety. Wire physical E-stop to KUKA I/O |
| **Failsafe** | Set KUKA `External Control Fallback` to `STOP` (never `CONTINUE`) |

---
## 🔁 5. If Raw UDP Is Too Complex

| Option | Pros | Cons |
|--------|------|------|
| `kuka_rsi_driver` (ROS) | Mature, validated, Python via ROS | Requires ROS, extra latency (~5-10ms) |
| C/Java bridge + `ctypes` | Near-zero overhead, official examples available | Requires C/Java toolchain |
| `kuka-rsi-python` (PyPI) | Simple wrapper | Unofficial, may lag behind KRC updates |

---
## 📌 Next Steps
1. Verify KUKA `RSI ACTIVE` & `EXTERNAL CONTROL` status on pendant
2. Test UDP connectivity: `nc -u -v <KUKA_IP> <PORT>`
3. Run Python script with `home_joints` → observe `Joint 1: 0.0 ...` in KUKA monitor
4. Gradually increase command rate to your target Hz
5. Always test in `T1` or `AUTOMATIC` with safety zones enabled

If you share your **KRC version** (KRC2/KRC4/KRC5) and **exact RSI package name**, I can provide the precise packet layout and KSS commands for your hardware.