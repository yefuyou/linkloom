"""The gate blocks external transport before DNS or an HTTP/SDK request."""
import socket

import pytest


def test_external_transport_is_denied(no_external_provider_transport):
    with pytest.raises(RuntimeError, match="OFFLINE_GATE_EXTERNAL_DNS_DENIED"):
        socket.getaddrinfo("generativelanguage.googleapis.com", 443)
    with socket.socket() as connection:
        with pytest.raises(RuntimeError, match="OFFLINE_GATE_EXTERNAL_NETWORK_DENIED"):
            connection.connect(("198.51.100.1", 443))
