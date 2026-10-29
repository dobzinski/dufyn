"""QUIC certificate collector; aioquic private TLS access is isolated here."""
import argparse
import asyncio
import json
import logging
import socket
import ssl
import threading
import time
from aioquic.asyncio import connect, QuicConnectionProtocol
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import HeadersReceived
from aioquic.quic.events import ConnectionTerminated
from aioquic.quic.configuration import QuicConfiguration
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import NameOID


class Http3Protocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.http = H3Connection(self._quic)
        self.response = None
        self.stream_id = None

    def quic_event_received(self, event):
        if isinstance(event, ConnectionTerminated) and self.response is not None and not self.response.done():
            self.response.set_exception(ConnectionError("HTTP/3 connection terminated"))
        for http_event in self.http.handle_event(event):
            if isinstance(http_event, HeadersReceived) and http_event.stream_id == self.stream_id:
                for key, value in http_event.headers:
                    if key == b":status" and self.response is not None and not self.response.done():
                        status = int(value)
                        if status >= 200:
                            self.response.set_result(status)

    async def head(self, server_name, port):
        self.response = asyncio.get_running_loop().create_future()
        self.stream_id = self._quic.get_next_available_stream_id()
        authority = server_name if port == 443 else f"{server_name}:{port}"
        self.http.send_headers(self.stream_id, [(b":method", b"HEAD"), (b":scheme", b"https"),
                               (b":authority", authority.encode("idna")), (b":path", b"/")], end_stream=True)
        self.transmit()
        return await self.response


async def collect(host, port, server_name, timeout=5, stop=None, web=False):
    started = time.monotonic()
    config = QuicConfiguration(is_client=True, alpn_protocols=["h3"], server_name=server_name,
                               verify_mode=ssl.CERT_NONE, idle_timeout=timeout)
    async def handshake():
        # Prefer IPv4, matching the scanner's current address model.
        addresses = await asyncio.get_running_loop().getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM)
        async with connect(addresses[0][4][0], port, configuration=config, wait_connected=False, create_protocol=Http3Protocol if web else QuicConnectionProtocol) as protocol:
            protocol.transmit()
            try:
                await protocol.wait_connected()
            finally:
                # aioquic shields this future; consume it after cancellation too.
                waiter = protocol._connected_waiter
                if waiter is not None:
                    waiter.add_done_callback(lambda f: None if f.cancelled() else f.exception())
            tls = protocol._quic.tls
            cert = tls._peer_certificate
            if cert is None:
                raise ValueError("QUIC handshake supplied no certificate")
            def cn(name):
                values = name.get_attributes_for_oid(NameOID.COMMON_NAME)
                return values[0].value if values else None
            try:
                sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
            except x509.ExtensionNotFound:
                sans = []
            certificate = {"subject_cn": cn(cert.subject), "issuer_cn": cn(cert.issuer),
                    "serial_number": str(cert.serial_number), "sans": sans,
                    "valid_from": cert.not_valid_before_utc.isoformat(),
                    "valid_until": cert.not_valid_after_utc.isoformat(),
                    "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex(),
                    "tls_version": "TLSv1.3", "cipher": tls.key_schedule.cipher_suite.name}
            if not web:
                return certificate
            status = await protocol.head(server_name, port)
            return {"state":"O", "http_status":status, "response_time_ms":round((time.monotonic()-started)*1000,3),
                    "check_protocol":"https", "certificate":certificate}
    task = asyncio.create_task(handshake())
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while not task.done():
            if stop is not None and stop.is_set():
                raise RuntimeError("QUIC collection interrupted")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("QUIC handshake timeout")
            await asyncio.wait({task}, timeout=0.1)
        return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def discover_quic_certificate(host, port, server_name, stop, timeout=5):
    if stop.is_set():
        raise RuntimeError("QUIC collection interrupted")
    try:
        return asyncio.run(collect(host, port, server_name, timeout, stop))
    except (OSError, TimeoutError, ConnectionError, ValueError) as error:
        logging.getLogger("scanner").info("QUIC certificate unavailable: host=%s port=%s reason=%s", host, port, type(error).__name__)
        return None


def check_http3(host, port, stop, timeout=5):
    if stop.is_set():
        raise RuntimeError("HTTP/3 check interrupted")
    try:
        return asyncio.run(collect(host, port, host, timeout, stop, web=True))
    except (OSError, TimeoutError, ConnectionError, ValueError):
        # No response cannot establish that a UDP service is Closed.
        return {"state":"U", "http_status":None, "response_time_ms":None,
                "check_protocol":"https", "certificate":None}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect a certificate using QUIC with HTTP/3 ALPN")
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--server-name")
    parser.add_argument("--timeout", type=float, default=5)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.timeout <= 0:
        parser.error("Invalid port or timeout")
    result = discover_quic_certificate(args.host, args.port, args.server_name or args.host, threading.Event(), args.timeout)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result else 1)
