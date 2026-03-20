```markdown
# IPv6 to IPv4 Multi-Port Proxy
Multi-protocol proxy for tunneling IPv4 traffic through IPv6 networks with original IP preservation

* [Installation](#installation)
* [Dependencies](#dependencies)
* [How to use](#how-to-use)
* [Config](#config)
  * [Client config](#client-config)
  * [Server config](#server-config)
  * [Port config](#port-config)
* [Info](#info)

## Dependencies
- [Python](https://www.python.org/downloads/) 3.6+
- Windows 10/11 x64 / Linux / macOS
- IPv6 network support

## Installation
```bash
git clone https://github.com/Rufemlike/ipv6-ipv4-proxy.git
cd ipv6-ipv4-proxy
python proxy.py --create-config
```

## How to use
1. Create configuration file (or use interactive setup: `python proxy.py --interactive`)
2. Edit config with your ports and remote IPv6 address
3. Start proxy: `python proxy.py --config proxy_config.json`
4. Configure your game/application to use localhost:port

## Config
### Client config
```json
{
  "mode": "client",
  "remote_ipv6": "2001:db8::1",
  "ports": [
    {
      "local": 2001,
      "remote": 2001,
      "protocol": "udp",
      "preserve_ip": true,
      "description": "Arma server"
    },
    {
      "local": 6122,
      "remote": 6122,
      "protocol": "tcp",
      "preserve_ip": true,
      "proxy_protocol": true,
      "description": "API server"
    }
  ]
}
```

### Server config
```json
{
  "mode": "server",
  "ports": [
    {
      "listen": 2001,
      "remote": 2001,
      "target_host": "192.168.1.100",
      "protocol": "udp",
      "preserve_ip": true,
      "description": "Forward UDP to game server"
    },
    {
      "listen": 6122,
      "remote": 6122,
      "target_host": "192.168.1.100",
      "protocol": "tcp",
      "preserve_ip": true,
      "proxy_protocol": true,
      "description": "Forward TCP to API server"
    }
  ]
}
```

### Both mode config (testing)
```json
{
  "mode": "both",
  "remote_ipv6": "::1",
  "ports": [
    {
      "local": 2001,
      "remote": 2001,
      "protocol": "udp",
      "preserve_ip": true,
      "description": "Local test UDP"
    },
    {
      "local": 6122,
      "remote": 6122,
      "protocol": "tcp",
      "preserve_ip": true,
      "proxy_protocol": true,
      "description": "Local test TCP"
    }
  ]
}
```

### Port config
```json
{
  "local": 2001,           # Local port to listen on
  "remote": 2001,          # Remote port to forward to (optional, defaults to local)
  "protocol": "udp",       # Protocol: "tcp" or "udp"
  "preserve_ip": true,     # Preserve original client IP
  "proxy_protocol": true,  # Use PROXY protocol (TCP only)
  "description": "Game"    # Optional description
}
```

### Game config
```json
# For Minecraft server (server.properties)
proxy-protocol=true

# For Source Engine games (startup parameters)
+sv_proxies 1 +net_public_adr <your_ipv6>

# For Arma server (no additional config needed)
# Just point game client to proxy address

# For Nginx (web-based games)
server {
    listen 80 proxy_protocol;
    set_real_ip_from 127.0.0.1;
    real_ip_header proxy_protocol;
}
```

## Testing
### Check if ports are listening
```bash
# Windows
netstat -an | findstr "2001 6122"

# Linux/macOS
netstat -tulpn | grep -E "2001|6122"
```

### Test TCP connection
```bash
# Windows PowerShell
Test-NetConnection -ComputerName localhost -Port 6122

# Linux/macOS
nc -zv localhost 6122
```

### Test UDP connection
```python
# Python test script
import socket

def test_udp(port):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(b'test', ('127.0.0.1', port))
        sock.close()
        print(f"✓ UDP packet sent to port {port}")
        return True
    except Exception as e:
        print(f"✗ UDP test failed: {e}")
        return False

if __name__ == "__main__":
    test_udp(2001)
```

### Full test script
```python
# test_proxy.py
import socket
import sys

def test_tcp(port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(('127.0.0.1', port))
        print(f"✓ TCP port {port}: OPEN")
        s.close()
        return True
    except ConnectionRefusedError:
        print(f"✗ TCP port {port}: CLOSED (connection refused)")
        return False
    except Exception as e:
        print(f"✗ TCP port {port}: {e}")
        return False

def test_udp(port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.sendto(b'TEST_PACKET', ('127.0.0.1', port))
        print(f"✓ UDP port {port}: OPEN (packet sent)")
        s.close()
        return True
    except Exception as e:
        print(f"✗ UDP port {port}: {e}")
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("Proxy Connection Test")
    print("=" * 50)
    
    test_tcp(6122)
    test_udp(2001)
```

## Command line options
```bash
# Create template configuration files
python proxy.py --create-config

# Interactive setup wizard
python proxy.py --interactive

# Run with specific config
python proxy.py --config proxy_config.json

# Specify mode directly
python proxy.py --mode client --config client_config.json
python proxy.py --mode server --config server_config.json
```

## Expected output
```
============================================================
Multi-Port IPv6 to IPv4 Proxy
============================================================
Mode: CLIENT
Configuration: proxy_config.json

Starting proxies...
TCP Proxy listening on 0.0.0.0:6122 -> 2001:db8::1:6122
  - Preserving original client IP using PROXY protocol
UDP Proxy listening on 0.0.0.0:2001 -> 2001:db8::1:2001
  - Preserving original client IP in UDP packets
Started UDP proxy on port 2001 -> 2001:db8::1:2001
Started TCP proxy on port 6122 -> 2001:db8::1:6122

Proxy is running. Press Ctrl+C to stop.

UDP: New packet from 192.168.1.50:54321 -> 2001:db8::1:2001 (128 bytes)
TCP: New connection on port 6122 from 192.168.1.50:54322
TCP: Client -> Target (6122): 256 bytes
```

## Info
- Proxy preserves original client IPv4 address using PROXY protocol v1 for TCP and custom headers for UDP
- Supports multiple ports simultaneously with different protocols (TCP/UDP)
- Game servers see real player IPs, not proxy address
- Use `--interactive` flag for guided configuration setup
- Both mode allows testing client and server on same machine (uses `::1` for local IPv6)
- UDP connections have 60 second timeout (configurable in code)
