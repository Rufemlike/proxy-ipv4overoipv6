"""
Copyright 2022 GamingCoookie
Copyright 2025 Rufemlike
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

import socket
import re
import selectors
import os
import pickle
from threading import Thread
import time


def safe_send(conn, msg):
    """
    Safely send data to a socket, handling blocking IO errors
    For non-blocking sockets, wait until socket is ready for writing
    """
    if not msg:
        return

    print(f"Sending {len(msg)} bytes")

    try:
        msg_len = len(msg)
        totalsent = 0

        while totalsent < msg_len:
            try:
                # Try to send data
                sent = conn.send(msg[totalsent:])
                if sent == 0:
                    raise RuntimeError("socket connection broken")
                totalsent += sent
            except BlockingIOError:
                # Socket not ready for writing, wait briefly
                time.sleep(0.001)
                continue
    except Exception as e:
        print(f"Error in safe_send: {e}")


def clear_screen():
    """Clear console screen (cross-platform)"""
    os.system('cls' if os.name == 'nt' else 'clear')
    print('Simple Reverse IPv6 to IPv4 TCP proxy\nVersion 1.0')
    print('-' * 100)


class ReverseProxy(Thread):
    """
    Reverse proxy thread that handles IPv6 to IPv4 connections
    Listens on IPv6 address and forwards traffic to local IPv4 server
    """

    def __init__(self, addr: str = None, port: int = None, standalone: bool = False):
        # Address to bind to (host, port)
        self.addr = (addr, port) if addr is not None else None
        # Target port to forward to
        self.port = port
        # Whether to run in standalone mode (with user interface)
        self.standalone = standalone
        # Selector for handling multiple sockets
        self.sel = None
        # Flag to control proxy running state
        self.running = True

        Thread.__init__(self, name=f'{addr=} {port=}')
        self.daemon = True  # Thread will exit when main program exits

    def accept_connection(self, sock):
        """Accept new client connection and create connection to local server"""
        try:
            # Accept new client connection
            client, addr = sock.accept()
            print(f"New connection from {addr}")
            client.setblocking(False)

            # Create connection to local IPv4 server
            server = socket.create_connection(("127.0.0.1", self.port))
            server.setblocking(False)

            # Register both sockets with selector for reading events
            # Client -> read_from_client, Server -> read_from_server
            self.sel.register(client, selectors.EVENT_READ,
                              (self.read_from_client, client, server))
            self.sel.register(server, selectors.EVENT_READ,
                              (self.read_from_server, client, server))

        except Exception as e:
            print(f"Error accepting connection: {e}")

    def read_from_client(self, client, server):
        """Read data from client, replace IPv6 address with localhost, send to server"""
        try:
            # Read data from client socket
            data = client.recv(4096)
            if data:
                # Replace IPv6 address in HTTP Host header with localhost:port
                ip6 = re.compile(f'\\[[^]]*]:{self.port}')
                str_data = data.decode('ISO-8859-1', 'ignore')
                ipm = ip6.search(str_data)
                if ipm:
                    # Replace IPv6 address with 127.0.0.1
                    str_data = str_data.replace(str_data[ipm.start():ipm.end()],
                                                f'127.0.0.1:{self.port}')
                    data = str_data.encode('ISO-8859-1')

                print(f"Client -> Server: {len(data)} bytes")
                # Forward modified data to server
                safe_send(server, data)
            else:
                # Client closed connection (received empty data)
                print("Client closed connection")
                self.close_connection(client, server)
        except (ConnectionResetError, ConnectionAbortedError):
            print("Client connection lost")
            self.close_connection(client, server)
        except Exception as e:
            print(f"Error reading from client: {e}")
            self.close_connection(client, server)

    def read_from_server(self, client, server):
        """Read data from server and forward to client"""
        try:
            # Read data from server socket
            data = server.recv(4096)
            if data:
                print(f"Server -> Client: {len(data)} bytes")
                # Forward data to client (no modification needed)
                safe_send(client, data)
            else:
                # Server closed connection
                print("Server closed connection")
                self.close_connection(client, server)
        except (ConnectionResetError, ConnectionAbortedError):
            print("Server connection lost")
            self.close_connection(client, server)
        except Exception as e:
            print(f"Error reading from server: {e}")
            self.close_connection(client, server)

    def close_connection(self, client, server):
        """Cleanup connection: unregister from selector and close sockets"""
        try:
            self.sel.unregister(client)
        except Exception:
            pass

        try:
            self.sel.unregister(server)
        except Exception:
            pass

        try:
            client.close()
        except Exception:
            pass

        try:
            server.close()
        except Exception:
            pass

    def run(self):
        """Main proxy thread execution"""
        if self.standalone:
            clear_screen()
            # Try to load configuration from file
            try:
                config_file = open('config.txt', 'rb')
                self.addr = pickle.loads(config_file.read())
                config_file.close()
            except FileNotFoundError:
                pass  # Config file doesn't exist
            except EOFError:
                self.addr = None  # Config file is empty

            # If no config loaded, ask user for configuration
            if not self.addr:
                self.port = input("Which port do you want? (Default: 7245)\nEnter: ")
                if self.port:
                    self.port = int(self.port)
                else:
                    self.port = 7245

                # Get available IPv6 addresses
                try:
                    addrs = socket.getaddrinfo(socket.gethostname(), self.port,
                                               family=socket.AF_INET6)
                    # Filter out link-local addresses (fe80::)
                    valid_addrs = []
                    for addr in addrs:
                        if not addr[4][0].startswith('fe80'):
                            valid_addrs.append(addr)

                    # Let user choose address if multiple available
                    if len(valid_addrs) > 1:
                        print("Please select which address to bind to:")
                        for i, addr in enumerate(valid_addrs):
                            print(f"[{i + 1}] {addr[4][0]}")
                        selection = int(input("Enter: ")) - 1
                        self.addr = valid_addrs[selection][4][:2]
                    elif valid_addrs:
                        self.addr = valid_addrs[0][4][:2]
                    else:
                        print("No valid IPv6 addresses found!")
                        return
                except Exception as e:
                    print(f"Error getting addresses: {e}")
                    return

                # Ask if user wants to save configuration
                if input('Do you want to save config for next time?(Y/N)\nEnter: ').upper() == 'Y':
                    try:
                        config_file = open('config.txt', 'wb')
                        config_file.write(pickle.dumps(self.addr))
                        config_file.close()
                    except Exception as e:
                        print(f"Error saving config: {e}")

                    clear_screen()

            print(f"Reverse Proxy is up and running on [{self.addr[0]}] with port {self.addr[1]}")

        # Create selector for handling multiple connections
        self.sel = selectors.DefaultSelector()

        try:
            # Create IPv6 listening socket
            ipv6side = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            # Allow address reuse (helpful for quick restarts)
            ipv6side.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Bind to specified address and port
            ipv6side.bind(self.addr)
            # Listen for incoming connections
            ipv6side.listen(20)
            # Set non-blocking mode
            ipv6side.setblocking(False)

            # Register listening socket with selector
            self.sel.register(ipv6side, selectors.EVENT_READ, self.accept_connection)

            print(f"Proxy server started successfully on [{self.addr[0]}]:{self.addr[1]}")

            # Main event loop
            while self.running:
                try:
                    # Wait for socket events with 1 second timeout
                    events = self.sel.select(timeout=1.0)
                    for key, mask in events:
                        callback = key.data
                        if isinstance(callback, tuple):
                            # Data handler callback (read_from_client/read_from_server)
                            callback[0](callback[1], callback[2])
                        else:
                            # Connection acceptor callback
                            callback(key.fileobj)
                except KeyboardInterrupt:
                    print("\nShutting down...")
                    self.running = False
                    break
                except Exception as e:
                    print(f"Error in select loop: {e}")
                    time.sleep(1)

        except Exception as e:
            print(f"Error starting proxy server: {e}")
        finally:
            # Cleanup: close all sockets and selector
            if self.sel:
                for key in list(self.sel.get_map().values()):
                    try:
                        key.fileobj.close()
                    except:
                        pass
                self.sel.close()


if __name__ == '__main__':
    try:
        # Create and start proxy in standalone mode
        p = ReverseProxy(standalone=True)
        p.start()
        p.join()
    except KeyboardInterrupt:
        print("\nProxy server stopped.")
