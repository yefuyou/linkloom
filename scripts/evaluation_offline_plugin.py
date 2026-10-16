"""Pytest-only network boundary for the offline gates; localhost HTTP is allowed."""
import ipaddress
import socket

import pytest


def _local(host):
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


@pytest.fixture(autouse=True)
def no_external_provider_transport(monkeypatch):
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    resolve = socket.getaddrinfo

    def check(address):
        if isinstance(address, tuple) and not _local(address[0]):
            raise RuntimeError(f"OFFLINE_GATE_EXTERNAL_NETWORK_DENIED: {address[0]}")

    def local_connect(self, address):
        check(address)
        return connect(self, address)

    def local_connect_ex(self, address):
        check(address)
        return connect_ex(self, address)

    def local_resolve(host, *args, **kwargs):
        if host is not None and not _local(host):
            raise RuntimeError(f"OFFLINE_GATE_EXTERNAL_DNS_DENIED: {host}")
        return resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", local_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", local_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", local_resolve)
