import socket
import time
import threading


class BackendServer:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.is_alive = True

    @property
    def url(self):
        return f"http://{self.host}:{self.port}"


def start_health_checks(servers, interval=2):
    def check_loop():
        while True:
            for server in servers:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                status = sock.connect_ex((server.host, server.port))
                sock.close()

                prev = server.is_alive
                server.is_alive = (status == 0)

                if prev and not server.is_alive:
                    print(f"[health] {server.url} went down, removing from pool")
                elif not prev and server.is_alive:
                    print(f"[health] {server.url} is back online")

            time.sleep(interval)

    t = threading.Thread(target=check_loop, daemon=True)
    t.start()