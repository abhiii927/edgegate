from __future__ import annotations

import socket
import threading


class BackendServer:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._is_alive = True
        self._lock = threading.Lock()

    @property
    def is_alive(self) -> bool:
        with self._lock:
            return self._is_alive

    @is_alive.setter
    def is_alive(self, value: bool) -> None:
        with self._lock:
            self._is_alive = value

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def probe_tcp(host: str, port: int, timeout: float = 1.0) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def start_health_checks(
    servers: list[BackendServer],
    interval: float = 2.0,
    timeout: float = 1.0,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    event = stop_event or threading.Event()

    def check_loop() -> None:
        while not event.is_set():
            for server in servers:
                alive = probe_tcp(server.host, server.port, timeout=timeout)
                prev = server.is_alive
                server.is_alive = alive

                if prev and not alive:
                    print(f"[health] {server.url} went down, removing from pool")
                elif not prev and alive:
                    print(f"[health] {server.url} is back online")

            event.wait(interval)

    thread = threading.Thread(target=check_loop, daemon=True)
    thread.start()
    return thread