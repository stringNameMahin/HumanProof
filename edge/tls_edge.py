"""TLS-terminating edge for the HumanProof network layer.

Browsers connect to https://127.0.0.1:8443. The edge reads the raw TLS ClientHello before
the handshake, computes JA3 and JA4 fingerprints, times the connection and handshake, then
forwards each HTTP/1.1 request to Django with the metadata in a signed header.

Run:  python edge/tls_edge.py            (after `manage.py init_demo` created backend/.edge-key)
"""

import argparse
import base64
import datetime
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import socketserver
import ssl
import struct
import sys
import time


REPO_DIR = Path(__file__).resolve().parents[1]
CERT_DIR = Path(__file__).resolve().parent / "certs"
HOP_BY_HOP = {"connection", "keep-alive", "proxy-connection", "proxy-authenticate", "proxy-authorization",
              "te", "trailer", "transfer-encoding", "upgrade"}
EDGE_HEADER = "X-HumanProof-Edge"
EDGE_SIGNATURE = "X-HumanProof-Edge-Signature"
MAX_HELLO_BYTES = 32768
MAX_HEADER_BYTES = 65536
MAX_BODY_BYTES = 512 * 1024
TLS_VERSIONS = {0x0304: "13", 0x0303: "12", 0x0302: "11", 0x0301: "10", 0x0300: "s3"}


class HelloError(ValueError):
    """The bytes are not a parsable TLS ClientHello."""


def is_grease(value):
    return value & 0x0F0F == 0x0A0A and value >> 8 == value & 0xFF


def _u16_list(data):
    return [struct.unpack_from("!H", data, index)[0] for index in range(0, len(data) - 1, 2)]


def hello_length(data):
    """Bytes needed for the complete ClientHello handshake message, or None if more are required."""
    handshake = bytearray()
    offset = 0
    while offset + 5 <= len(data):
        content_type, _, length = struct.unpack_from("!BHH", data, offset)
        if content_type != 0x16:
            raise HelloError("The connection did not start with a TLS handshake record.")
        if offset + 5 + length > len(data):
            return None
        handshake += data[offset + 5:offset + 5 + length]
        offset += 5 + length
        if len(handshake) >= 4:
            if handshake[0] != 0x01:
                raise HelloError("The first handshake message is not a ClientHello.")
            needed = int.from_bytes(handshake[1:4], "big") + 4
            if len(handshake) >= needed:
                return offset
    return None


def parse_client_hello(data):
    """Parse TLS records holding a ClientHello into the fields fingerprints need."""
    handshake = bytearray()
    offset = 0
    while offset + 5 <= len(data):
        _, _, length = struct.unpack_from("!BHH", data, offset)
        handshake += data[offset + 5:offset + 5 + length]
        offset += 5 + length
    if len(handshake) < 4 or handshake[0] != 0x01:
        raise HelloError("Missing ClientHello.")
    body = bytes(handshake[4:4 + int.from_bytes(handshake[1:4], "big")])
    try:
        legacy_version = struct.unpack_from("!H", body, 0)[0]
        position = 34
        session_length = body[position]
        position += 1 + session_length
        cipher_length = struct.unpack_from("!H", body, position)[0]
        ciphers = _u16_list(body[position + 2:position + 2 + cipher_length])
        position += 2 + cipher_length
        position += 1 + body[position]
        extensions, groups, point_formats, signatures, alpn, versions = [], [], [], [], [], []
        server_name = None
        if position + 2 <= len(body):
            end = position + 2 + struct.unpack_from("!H", body, position)[0]
            position += 2
            while position + 4 <= end:
                kind, length = struct.unpack_from("!HH", body, position)
                value = body[position + 4:position + 4 + length]
                position += 4 + length
                extensions.append(kind)
                if kind == 0x0000 and len(value) >= 5:
                    name_length = struct.unpack_from("!H", value, 3)[0]
                    server_name = value[5:5 + name_length].decode("ascii", "replace")
                elif kind == 0x000A:
                    groups = _u16_list(value[2:])
                elif kind == 0x000B:
                    point_formats = list(value[1:1 + value[0]]) if value else []
                elif kind == 0x000D:
                    signatures = _u16_list(value[2:])
                elif kind == 0x0010:
                    cursor = 2
                    while cursor < len(value):
                        size = value[cursor]
                        alpn.append(value[cursor + 1:cursor + 1 + size].decode("ascii", "replace"))
                        cursor += 1 + size
                elif kind == 0x002B and value:
                    versions = _u16_list(value[1:1 + value[0]])
    except (IndexError, struct.error) as error:
        raise HelloError("Truncated ClientHello.") from error
    return {"legacy_version": legacy_version, "ciphers": ciphers, "extensions": extensions, "groups": groups,
            "point_formats": point_formats, "signature_algorithms": signatures, "alpn": alpn,
            "supported_versions": versions, "server_name": server_name}


def ja3(hello):
    clean = lambda values: [value for value in values if not is_grease(value)]
    fields = [str(hello["legacy_version"]), "-".join(map(str, clean(hello["ciphers"]))),
              "-".join(map(str, clean(hello["extensions"]))), "-".join(map(str, clean(hello["groups"]))),
              "-".join(map(str, hello["point_formats"]))]
    text = ",".join(fields)
    return text, hashlib.md5(text.encode()).hexdigest()


def _truncated_sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()[:12] if text else "000000000000"


def ja4(hello):
    """JA4 TLS client fingerprint (FoxIO specification, TCP transport)."""
    ciphers = [value for value in hello["ciphers"] if not is_grease(value)]
    extensions = [value for value in hello["extensions"] if not is_grease(value)]
    versions = [value for value in hello["supported_versions"] if not is_grease(value)]
    version = TLS_VERSIONS.get(max(versions) if versions else hello["legacy_version"], "00")
    alpn = hello["alpn"][0] if hello["alpn"] else ""
    alpn_code = f"{alpn[0]}{alpn[-1]}" if alpn and alpn.isascii() and alpn[0].isalnum() and alpn[-1].isalnum() else (
        alpn.encode().hex()[:1] + alpn.encode().hex()[-1:] if alpn else "00")
    part_a = (f"t{version}{'d' if hello['server_name'] else 'i'}"
              f"{min(len(ciphers), 99):02d}{min(len(extensions), 99):02d}{alpn_code}")
    part_b = _truncated_sha256(",".join(sorted(f"{value:04x}" for value in ciphers)))
    sorted_extensions = ",".join(sorted(f"{value:04x}" for value in extensions if value not in (0x0000, 0x0010)))
    signatures = ",".join(f"{value:04x}" for value in hello["signature_algorithms"] if not is_grease(value))
    part_c = _truncated_sha256(f"{sorted_extensions}_{signatures}" if signatures else sorted_extensions)
    return f"{part_a}_{part_b}_{part_c}"


def fingerprint(hello):
    ja3_text, ja3_hash = ja3(hello)
    versions = [value for value in hello["supported_versions"] if not is_grease(value)]
    version = max(versions) if versions else hello["legacy_version"]
    return {
        "ja4": ja4(hello), "ja3": ja3_text[:2048], "ja3_hash": ja3_hash,
        "tls_version": {0x0304: "1.3", 0x0303: "1.2", 0x0302: "1.1", 0x0301: "1.0"}.get(version, "other"),
        "alpn": hello["alpn"][0] if hello["alpn"] else None,
        "grease": any(is_grease(value) for value in hello["ciphers"] + hello["extensions"]),
        "cipher_count": len([value for value in hello["ciphers"] if not is_grease(value)]),
        "extension_count": len([value for value in hello["extensions"] if not is_grease(value)]),
        "sni": bool(hello["server_name"]),
    }


def sign(metadata, key):
    encoded = base64.urlsafe_b64encode(json.dumps(metadata, separators=(",", ":"), sort_keys=True).encode()).decode()
    return encoded, hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()


def ensure_certificate(cert_dir=CERT_DIR):
    """Create a self-signed certificate for localhost/127.0.0.1 if none exists."""
    cert_path, key_path = cert_dir / "edge-cert.pem", cert_dir / "edge-key.pem"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    cert_dir.mkdir(parents=True, exist_ok=True)
    private_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HumanProof local edge")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    key_path.write_bytes(private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def edge_key():
    value = os.environ.get("HUMANPROOF_EDGE_KEY", "").strip()
    path = REPO_DIR / "backend" / ".edge-key"
    if not value and path.is_file():
        value = path.read_text(encoding="ascii").strip()
    return value


def read_hello(connection, timeout=5.0):
    """Peek at the ClientHello without consuming it, so the TLS library can still read it."""
    connection.settimeout(timeout)
    started = time.perf_counter()
    first_byte = None
    data = b""
    while len(data) < MAX_HELLO_BYTES:
        data = connection.recv(MAX_HELLO_BYTES, socket.MSG_PEEK)
        if not data:
            raise HelloError("The client closed the connection before sending a ClientHello.")
        if first_byte is None:
            first_byte = time.perf_counter()
        needed = hello_length(data)
        if needed is not None:
            return data[:needed], (first_byte - started) * 1000
        time.sleep(0.002)
    raise HelloError("ClientHello exceeds the size limit.")


def read_request(stream):
    line = stream.readline(8192)
    if not line:
        return None
    method, target, version = line.decode("latin-1").rstrip("\r\n").split(" ", 2)
    headers, total = [], 0
    while True:
        raw = stream.readline(8192)
        total += len(raw)
        if total > MAX_HEADER_BYTES:
            raise ValueError("Request headers are too large.")
        if raw in (b"\r\n", b"\n", b""):
            break
        name, _, value = raw.decode("latin-1").partition(":")
        headers.append((name.strip(), value.strip()))
    lookup = {name.lower(): value for name, value in headers}
    if "chunked" in lookup.get("transfer-encoding", "").lower():
        body = b""
        while True:
            size = int(stream.readline(64).split(b";")[0].strip(), 16)
            if size == 0:
                stream.readline(64)
                break
            body += stream.read(size)
            stream.readline(64)
            if len(body) > MAX_BODY_BYTES:
                raise ValueError("Request body is too large.")
    else:
        length = int(lookup.get("content-length", "0") or 0)
        if length > MAX_BODY_BYTES:
            raise ValueError("Request body is too large.")
        body = stream.read(length) if length else b""
    return method, target, version, headers, body


class EdgeHandler(socketserver.BaseRequestHandler):
    def handle(self):
        server = self.server
        accepted = time.perf_counter()
        try:
            hello_bytes, hello_delay = read_hello(self.request)
            metadata = fingerprint(parse_client_hello(hello_bytes))
            hello_at = time.perf_counter()
            tls = server.context.wrap_socket(self.request, server_side=True)
            tls.settimeout(30)
        except (HelloError, ssl.SSLError, OSError):
            return
        metadata.update({
            "hello_delay_ms": round(hello_delay, 3),
            "handshake_ms": round((time.perf_counter() - hello_at) * 1000, 3),
            "connection_ms": round((time.perf_counter() - accepted) * 1000, 3),
            "negotiated_version": tls.version(), "negotiated_cipher": (tls.cipher() or [None])[0],
        })
        stream = tls.makefile("rb")
        try:
            requests = 0
            while True:
                try:
                    request = read_request(stream)
                except (ValueError, OSError):
                    self.respond(tls, 400, b'{"detail": "Malformed request."}')
                    return
                if request is None:
                    return
                requests += 1
                metadata["requests_on_connection"] = requests
                keep_alive = self.forward(tls, request, metadata)
                if not keep_alive:
                    return
        except (OSError, ssl.SSLError, http.client.HTTPException):
            return
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def respond(self, tls, status, body):
        tls.sendall(f"HTTP/1.1 {status} {http.client.responses.get(status, '')}\r\n"
                    f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
                    .encode("latin-1") + body)

    def forward(self, tls, request, metadata):
        method, target, version, headers, body = request
        server = self.server
        encoded, signature = sign(metadata, server.key)
        upstream = http.client.HTTPConnection(server.upstream_host, server.upstream_port, timeout=30)
        try:
            upstream.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
            for name, value in headers:
                if name.lower() in HOP_BY_HOP or name.lower().startswith("x-humanproof-edge"):
                    continue
                if name.lower() == "content-length":
                    continue
                upstream.putheader(name, value)
            upstream.putheader("Content-Length", str(len(body)))
            upstream.putheader("X-Forwarded-Proto", "https")
            upstream.putheader(EDGE_HEADER, encoded)
            upstream.putheader(EDGE_SIGNATURE, signature)
            upstream.endheaders(body)
            response = upstream.getresponse()
            payload = response.read()
        except (OSError, http.client.HTTPException):
            self.respond(tls, 502, b'{"detail": "The HumanProof API behind the edge is unavailable."}')
            return False
        finally:
            upstream.close()
        client_close = version == "HTTP/1.0" or any(
            name.lower() == "connection" and value.lower() == "close" for name, value in headers)
        lines = [f"HTTP/1.1 {response.status} {response.reason}"]
        for name, value in response.getheaders():
            if name.lower() not in HOP_BY_HOP and name.lower() != "content-length":
                lines.append(f"{name}: {value}")
        lines.append(f"Content-Length: {len(payload)}")
        lines.append("Connection: close" if client_close else "Connection: keep-alive")
        tls.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + payload)
        return not client_close


class EdgeServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, upstream, key, cert_path, key_path):
        super().__init__(address, EdgeHandler)
        self.upstream_host, self.upstream_port = upstream
        self.key = key
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.set_alpn_protocols(["http/1.1"])
        self.context.load_cert_chain(cert_path, key_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description="TLS edge that fingerprints ClientHello messages for HumanProof.")
    parser.add_argument("--listen", default="127.0.0.1:8443", help="Address to accept HTTPS on (default 127.0.0.1:8443).")
    parser.add_argument("--upstream", default="127.0.0.1:8000", help="Django address (default 127.0.0.1:8000).")
    args = parser.parse_args(argv)
    key = edge_key()
    if not key:
        print("Missing edge key. Run: python backend/manage.py init_demo", file=sys.stderr)
        return 1
    host, port = args.listen.rsplit(":", 1)
    upstream_host, upstream_port = args.upstream.rsplit(":", 1)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print("The demo edge only listens on loopback addresses.", file=sys.stderr)
        return 1
    cert_path, key_path = ensure_certificate()
    with EdgeServer((host, int(port)), (upstream_host, int(upstream_port)), key, cert_path, key_path) as server:
        print(f"HumanProof TLS edge on https://{host}:{port} -> http://{args.upstream}")
        print("The certificate is self-signed; accept the browser warning once for this local demo.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
