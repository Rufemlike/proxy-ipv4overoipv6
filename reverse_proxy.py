"""
Copyright 2022 GamingCoookie
Copyright 2026 Rufemlike
Multi-Port Game Server IPv6 to IPv4 Proxy with TCP/UDP Support
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

import socket
import re
import selectors
import os
import pickle
import json
import argparse
import struct
import ipaddress
import threading
from threading import Thread
import time
import sys
import queue
import signal
from typing import Dict, List, Tuple, Optional


class TCPProxyHandler:
    """Handles TCP connections for a specific port"""

    def __init__(self, listen_port: int, target_host: str, target_port: int,
                 preserve_ip: bool = True, proxy_protocol: bool = True):
        self.listen_port = listen_port
        self.target_host = target_host
        self.target_port = target_port
        self.preserve_ip = preserve_ip
        self.proxy_protocol = proxy_protocol
        self.sel = None
        self.running = True
        self.client_ips = {}

    def start(self):
        """Start TCP proxy for this port"""
        self.sel = selectors.DefaultSelector()

        try:
            # Create listening socket (IPv4 for client mode, IPv6 for server mode)
            # We'll create both and let the main manager decide which to use
            self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_sock.bind(('0.0.0.0', self.listen_port))
            self.server_sock.listen(100)
            self.server_sock.setblocking(False)

            self.sel.register(self.server_sock, selectors.EVENT_READ, self.accept_connection)

            print(f"TCP Proxy listening on 0.0.0.0:{self.listen_port} -> {self.target_host}:{self.target_port}")
            if self.preserve_ip:
                print(
                    f"  - Preserving original client IP using {'PROXY protocol' if self.proxy_protocol else 'custom headers'}")

            # Main loop
            while self.running:
                try:
                    events = self.sel.select(timeout=1.0)
                    for key, mask in events:
                        callback = key.data
                        if isinstance(callback, tuple):
                            callback[0](callback[1], callback[2])
                        else:
                            callback(key.fileobj)
                except Exception as e:
                    print(f"Error in TCP proxy loop for port {self.listen_port}: {e}")
                    time.sleep(0.1)

        except Exception as e:
            print(f"Error starting TCP proxy on port {self.listen_port}: {e}")
        finally:
            self.cleanup()

    def accept_connection(self, sock):
        """Accept new TCP connection"""
        try:
            client, addr = sock.accept()
            print(f"TCP: New connection on port {self.listen_port} from {addr}")
            client.setblocking(False)

            # Create connection to target
            target = socket.create_connection((self.target_host, self.target_port))
            target.setblocking(False)

            # Store client info
            self.client_ips[client.fileno()] = addr

            # If preserving IP and using PROXY protocol, send header first
            if self.preserve_ip and self.proxy_protocol:
                proxy_header = f"PROXY TCP4 {addr[0]} {self.target_host} {addr[1]} {self.target_port}\r\n"
                try:
                    target.send(proxy_header.encode())
                except:
                    pass

            # Register both sockets
            self.sel.register(client, selectors.EVENT_READ,
                              (self.forward_client_to_target, client, target))
            self.sel.register(target, selectors.EVENT_READ,
                              (self.forward_target_to_client, client, target))

        except Exception as e:
            print(f"Error accepting connection on port {self.listen_port}: {e}")

    def forward_client_to_target(self, client, target):
        """Forward data from client to target"""
        try:
            data = client.recv(4096)
            if data:
                print(f"TCP: Client -> Target ({self.listen_port}): {len(data)} bytes")

                # If preserving IP but not using PROXY protocol, inject headers
                if self.preserve_ip and not self.proxy_protocol and client.fileno() in self.client_ips:
                    # Check if this is first packet (HTTP/SIP/RTSP etc)
                    # Inject X-Forwarded-For header for HTTP
                    if data.startswith(b'GET') or data.startswith(b'POST') or data.startswith(b'PUT'):
                        data = data.replace(b'Host:',
                                            f'X-Forwarded-For: {self.client_ips[client.fileno()][0]}\r\nHost:'.encode())

                safe_send(target, data)
            else:
                self.close_connection(client, target)
        except Exception as e:
            print(f"Error forwarding client->target: {e}")
            self.close_connection(client, target)

    def forward_target_to_client(self, client, target):
        """Forward data from target to client"""
        try:
            data = target.recv(4096)
            if data:
                print(f"TCP: Target -> Client ({self.listen_port}): {len(data)} bytes")
                safe_send(client, data)
            else:
                self.close_connection(client, target)
        except Exception as e:
            print(f"Error forwarding target->client: {e}")
            self.close_connection(client, target)

    def close_connection(self, client, target):
        """Close connection and cleanup"""
        try:
            if client.fileno() in self.client_ips:
                del self.client_ips[client.fileno()]
        except:
            pass

        for sock in [client, target]:
            try:
                self.sel.unregister(sock)
                sock.close()
            except:
                pass

    def cleanup(self):
        """Cleanup all resources"""
        if self.sel:
            for key in list(self.sel.get_map().values()):
                try:
                    key.fileobj.close()
                except:
                    pass
            self.sel.close()

    def stop(self):
        """Stop the proxy"""
        self.running = False


class UDPProxyHandler:
    """Handles UDP traffic for a specific port"""

    def __init__(self, listen_port: int, target_host: str, target_port: int,
                 preserve_ip: bool = True, timeout: int = 60):
        self.listen_port = listen_port
        self.target_host = target_host
        self.target_port = target_port
        self.preserve_ip = preserve_ip
        self.timeout = timeout
        self.running = True
        self.tunnels = {}
        self.lock = threading.Lock()

        # UDP sockets
        self.ipv4_sock = None
        self.ipv6_sock = None

    def start(self):
        """Start UDP proxy for this port"""
        try:
            # Create UDP socket (will be used in both directions)
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(('0.0.0.0', self.listen_port))
            self.sock.setblocking(False)

            print(f"UDP Proxy listening on 0.0.0.0:{self.listen_port} -> {self.target_host}:{self.target_port}")
            if self.preserve_ip:
                print(f"  - Preserving original client IP in UDP packets")

            # Main loop
            while self.running:
                try:
                    # Receive UDP packet
                    data, addr = self.sock.recvfrom(65535)
                    self.handle_udp_packet(data, addr)
                except BlockingIOError:
                    time.sleep(0.001)
                    continue
                except Exception as e:
                    print(f"Error in UDP proxy for port {self.listen_port}: {e}")
                    time.sleep(0.1)

        except Exception as e:
            print(f"Error starting UDP proxy on port {self.listen_port}: {e}")
        finally:
            if self.sock:
                self.sock.close()

    def handle_udp_packet(self, data: bytes, addr: Tuple[str, int]):
        """Handle incoming UDP packet"""
        try:
            # Check if this packet has our header (from client proxy)
            if len(data) >= 8 and data[:4] == b'UDPP':
                # Extract original client info
                ip_type = data[4:5]
                if ip_type == b'\x04':  # IPv4
                    original_ip = socket.inet_ntoa(data[5:9])
                    original_port = struct.unpack('>H', data[9:11])[0]
                    payload = data[11:]
                else:  # IPv6
                    original_ip = socket.inet_ntop(socket.AF_INET6, data[5:21])
                    original_port = struct.unpack('>H', data[21:23])[0]
                    payload = data[23:]

                print(
                    f"UDP: Restored original client {original_ip}:{original_port} -> {self.target_host}:{self.target_port}")

                # Forward to target with original IP info if preserving
                if self.preserve_ip:
                    # For game servers that support it, add custom header
                    # For example, for some games, you can prepend the original IP
                    payload = struct.pack('>I', original_port) + payload

                # Send to target
                self.sock.sendto(payload, (self.target_host, self.target_port))

            else:
                # Regular packet from local client
                print(f"UDP: New packet from {addr} -> {self.target_host}:{self.target_port} ({len(data)} bytes)")

                if self.preserve_ip:
                    # Add header with original client info
                    if '.' in addr[0]:
                        ip_bytes = socket.inet_aton(addr[0])
                        ip_type = b'\x04'
                    else:
                        ip_bytes = socket.inet_pton(socket.AF_INET6, addr[0])
                        ip_type = b'\x06'

                    port_bytes = struct.pack('>H', addr[1])
                    header = b'UDPP' + ip_type + ip_bytes + port_bytes
                    packet = header + data
                else:
                    packet = data

                self.sock.sendto(packet, (self.target_host, self.target_port))

        except Exception as e:
            print(f"Error handling UDP packet: {e}")

    def stop(self):
        """Stop the UDP proxy"""
        self.running = False
        if self.sock:
            self.sock.close()


class MultiPortProxy:
    """Manages multiple proxy instances for different ports"""

    def __init__(self, config_file: str = 'proxy_config.json', mode: str = 'client'):
        self.config_file = config_file
        self.mode = mode
        self.tcp_proxies = []
        self.udp_proxies = []
        self.running = True

        # Load configuration
        self.config = self.load_config()

    def load_config(self) -> dict:
        """Load configuration from JSON file"""
        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)
                print(f"Loaded configuration from {self.config_file}")
                return config
        except FileNotFoundError:
            print(f"Config file {self.config_file} not found, using defaults")
            return self.get_default_config()
        except Exception as e:
            print(f"Error loading config: {e}")
            return self.get_default_config()

    def get_default_config(self) -> dict:
        """Return default configuration"""
        return {
            "mode": self.mode,
            "remote_ipv6": "2001:db8::1",  # Remote IPv6 address for client mode
            "ports": [
                {
                    "local": 2001,
                    "remote": 2001,
                    "protocol": "udp",
                    "preserve_ip": True
                },
                {
                    "local": 6122,
                    "remote": 6122,
                    "protocol": "tcp",
                    "preserve_ip": True,
                    "proxy_protocol": True
                }
            ]
        }

    def start_proxies(self):
        """Start all proxy instances"""
        if self.mode == 'client':
            # Client mode: forward local ports to remote IPv6 server
            remote_ipv6 = self.config.get('remote_ipv6', '::1')
            ports = self.config.get('ports', [])

            for port_config in ports:
                local_port = port_config['local']
                remote_port = port_config.get('remote', local_port)
                protocol = port_config.get('protocol', 'tcp').lower()
                preserve_ip = port_config.get('preserve_ip', True)

                if protocol == 'tcp':
                    proxy = TCPProxyHandler(
                        listen_port=local_port,
                        target_host=remote_ipv6,
                        target_port=remote_port,
                        preserve_ip=preserve_ip,
                        proxy_protocol=port_config.get('proxy_protocol', True)
                    )
                    self.tcp_proxies.append(proxy)
                    Thread(target=proxy.start, daemon=True).start()

                elif protocol == 'udp':
                    proxy = UDPProxyHandler(
                        listen_port=local_port,
                        target_host=remote_ipv6,
                        target_port=remote_port,
                        preserve_ip=preserve_ip
                    )
                    self.udp_proxies.append(proxy)
                    Thread(target=proxy.start, daemon=True).start()

                print(f"Started {protocol.upper()} proxy on port {local_port} -> {remote_ipv6}:{remote_port}")

        else:
            # Server mode: forward to local game servers
            ports = self.config.get('ports', [])

            for port_config in ports:
                local_port = port_config.get('listen', port_config['local'])
                remote_port = port_config['remote']
                target_host = port_config.get('target_host', '127.0.0.1')
                protocol = port_config.get('protocol', 'tcp').lower()
                preserve_ip = port_config.get('preserve_ip', True)

                if protocol == 'tcp':
                    proxy = TCPProxyHandler(
                        listen_port=local_port,
                        target_host=target_host,
                        target_port=remote_port,
                        preserve_ip=preserve_ip,
                        proxy_protocol=port_config.get('proxy_protocol', True)
                    )
                    self.tcp_proxies.append(proxy)
                    Thread(target=proxy.start, daemon=True).start()

                elif protocol == 'udp':
                    proxy = UDPProxyHandler(
                        listen_port=local_port,
                        target_host=target_host,
                        target_port=remote_port,
                        preserve_ip=preserve_ip
                    )
                    self.udp_proxies.append(proxy)
                    Thread(target=proxy.start, daemon=True).start()

                print(f"Started {protocol.upper()} proxy on port {local_port} -> {target_host}:{remote_port}")

    def stop(self):
        """Stop all proxies"""
        print("\nStopping all proxies...")
        self.running = False

        for proxy in self.tcp_proxies:
            proxy.stop()

        for proxy in self.udp_proxies:
            proxy.stop()

        print("All proxies stopped")


def safe_send(conn, msg):
    """Safely send data to a socket"""
    if not msg:
        return

    try:
        totalsent = 0
        msg_len = len(msg)

        while totalsent < msg_len:
            sent = conn.send(msg[totalsent:])
            if sent == 0:
                raise RuntimeError("Socket connection broken")
            totalsent += sent
    except Exception as e:
        print(f"Error in safe_send: {e}")


def create_config_template():
    """Create a template configuration file"""
    config = {
        "mode": "client",  # or "server"
        "remote_ipv6": "2001:db8::1",  # Only used in client mode
        "ports": [
            {
                "local": 2001,
                "remote": 2001,
                "protocol": "udp",
                "preserve_ip": True,
                "description": "UDP game port"
            },
            {
                "local": 6122,
                "remote": 6122,
                "protocol": "tcp",
                "preserve_ip": True,
                "proxy_protocol": True,
                "description": "TCP game port with PROXY protocol"
            },
            {
                "local": 8080,
                "remote": 80,
                "protocol": "tcp",
                "preserve_ip": True,
                "proxy_protocol": False,
                "description": "HTTP proxy without PROXY protocol"
            }
        ]
    }

    # For server mode, add target_host
    server_config = {
        "mode": "server",
        "ports": [
            {
                "listen": 7245,
                "remote": 2001,
                "target_host": "192.168.1.100",
                "protocol": "udp",
                "preserve_ip": True,
                "description": "Forward UDP 7245 to game server UDP 2001"
            },
            {
                "listen": 7246,
                "remote": 6122,
                "target_host": "192.168.1.100",
                "protocol": "tcp",
                "preserve_ip": True,
                "proxy_protocol": True,
                "description": "Forward TCP 7246 to game server TCP 6122"
            }
        ]
    }

    with open('proxy_config_template.json', 'w') as f:
        json.dump(config, f, indent=4)

    with open('proxy_config_server_template.json', 'w') as f:
        json.dump(server_config, f, indent=4)

    print("Created configuration templates:")
    print("  - proxy_config_template.json (client mode)")
    print("  - proxy_config_server_template.json (server mode)")


def interactive_setup():
    """Interactive setup for multi-port proxy"""
    print("\n" + "=" * 60)
    print("Multi-Port IPv6 to IPv4 Proxy Setup")
    print("=" * 60)

    mode = input("Mode (client/server) [client]: ").strip().lower() or "client"

    config = {
        "mode": mode,
        "ports": []
    }

    if mode == "client":
        remote_ipv6 = input("Remote IPv6 address: ").strip()
        config["remote_ipv6"] = remote_ipv6

    print("\nConfigure ports (enter empty port to finish):")
    port_num = 1

    while True:
        print(f"\nPort {port_num}:")
        local_port = input("  Local port: ").strip()
        if not local_port:
            break

        remote_port = input(f"  Remote port (default {local_port}): ").strip() or local_port
        protocol = input("  Protocol (tcp/udp) [tcp]: ").strip().lower() or "tcp"
        preserve_ip = input("  Preserve original IP? (y/n) [y]: ").strip().lower() or "y"

        port_config = {
            "local": int(local_port),
            "remote": int(remote_port),
            "protocol": protocol,
            "preserve_ip": preserve_ip == 'y'
        }

        if mode == "server":
            target_host = input("  Target host [127.0.0.1]: ").strip() or "127.0.0.1"
            port_config["target_host"] = target_host

            if protocol == "tcp":
                proxy_protocol = input("  Use PROXY protocol? (y/n) [y]: ").strip().lower() or "y"
                port_config["proxy_protocol"] = proxy_protocol == 'y'

        config["ports"].append(port_config)
        port_num += 1

    if config["ports"]:
        filename = input("\nSave config to [proxy_config.json]: ").strip() or "proxy_config.json"
        with open(filename, 'w') as f:
            json.dump(config, f, indent=4)
        print(f"Configuration saved to {filename}")

    return config


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Multi-Port IPv6 to IPv4 Game Server Proxy')
    parser.add_argument('--mode', choices=['client', 'server'], help='Proxy mode')
    parser.add_argument('--config', default='proxy_config.json', help='Configuration file')
    parser.add_argument('--create-config', action='store_true', help='Create template configuration file')
    parser.add_argument('--interactive', action='store_true', help='Run interactive setup')

    args = parser.parse_args()

    if args.create_config:
        create_config_template()
        return

    if args.interactive:
        config = interactive_setup()
        if not config.get('ports'):
            print("No ports configured, exiting.")
            return
        proxy = MultiPortProxy(args.config, config.get('mode', 'client'))
        proxy.config = config
    else:
        proxy = MultiPortProxy(args.config, args.mode or 'client')

    print("\n" + "=" * 60)
    print("Multi-Port IPv6 to IPv4 Proxy")
    print("=" * 60)
    print(f"Mode: {proxy.mode.upper()}")
    print(f"Configuration: {proxy.config_file}")
    print("\nStarting proxies...")

    try:
        proxy.start_proxies()
        print("\nProxy is running. Press Ctrl+C to stop.\n")

        # Keep main thread alive
        while proxy.running:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nShutting down...")
        proxy.stop()
        print("Goodbye!")


if __name__ == '__main__':
    main()
