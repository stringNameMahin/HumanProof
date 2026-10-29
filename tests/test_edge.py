"""TLS edge: ClientHello parsing, JA3/JA4, and signed forwarding."""

import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import http.client
import json
from pathlib import Path
import ssl
import struct
import tempfile
from threading import Thread
import unittest

from edge.tls_edge import (
    EdgeServer, HelloError, ensure_certificate, fingerprint, hello_length, is_grease, ja3, ja4,
    parse_client_hello,
)


def extension(kind, payload):
    return struct.pack("!HH", kind, len(payload)) + payload


def u16_vector(values, length_bytes=2):
    data = b"".join(struct.pack("!H", value) for value in values)
    return len(data).to_bytes(length_bytes, "big") + data


def chrome_like_hello():
    """A ClientHello with GREASE values in the positions Chromium uses them."""
    server_name = b"example.com"
    sni = struct.pack("!HBH", len(server_name) + 3, 0, len(server_name)) + server_name
    alpn_list = b"".join(bytes([len(item)]) + item for item in (b"h2", b"http/1.1"))
    extensions = b"".join([
        extension(0x1A1A, b""),
        extension(0x0000, sni),
        extension(0x000A, u16_vector([0x2A2A, 0x001D, 0x0017])),
        extension(0x000B, bytes([1, 0])),
        extension(0x000D, u16_vector([0x0403, 0x0804])),
        extension(0x0010, struct.pack("!H", len(alpn_list)) + alpn_list),
        extension(0x002B, u16_vector([0x3A3A, 0x0304, 0x0303], length_bytes=1)),
    ])
    body = (struct.pack("!H", 0x0303) + bytes(32) + b"\x00" + u16_vector([0x0A0A, 0x1301, 0x1302, 0xC02B])
            + b"\x01\x00" + struct.pack("!H", len(extensions)) + extensions)
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + struct.pack("!H", len(handshake)) + handshake


def openssl_hello():
    context = ssl.create_default_context()
    context.set_alpn_protocols(["h2", "http/1.1"])
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = context.wrap_bio(incoming, outgoing, server_hostname="localhost")
    try:
        client.do_handshake()
    except ssl.SSLWantReadError:
        pass
    return outgoing.read()


class ClientHelloTests(unittest.TestCase):
    def test_grease_detection(self):
        self.assertTrue(all(is_grease(0x0A0A + 0x1010 * index) for index in range(16)))
        self.assertFalse(is_grease(0x1301))
        self.assertFalse(is_grease(0x0A1A))

    def test_chromium_style_hello_matches_the_specifications(self):
        hello = parse_client_hello(chrome_like_hello())
        self.assertEqual(hello["server_name"], "example.com")
        self.assertEqual(hello["alpn"], ["h2", "http/1.1"])
        text, digest = ja3(hello)
        self.assertEqual(text, "771,4865-4866-49195,0-10-11-13-16-43,29-23,0")
        self.assertEqual(digest, hashlib.md5(text.encode()).hexdigest())
        cipher_hash = hashlib.sha256(b"1301,1302,c02b").hexdigest()[:12]
        extension_hash = hashlib.sha256(b"000a,000b,000d,002b_0403,0804").hexdigest()[:12]
        self.assertEqual(ja4(hello), f"t13d0306h2_{cipher_hash}_{extension_hash}")
        summary = fingerprint(hello)
        self.assertTrue(summary["grease"])
        self.assertEqual((summary["tls_version"], summary["cipher_count"], summary["extension_count"]), ("1.3", 3, 6))

    def test_openssl_client_has_no_grease(self):
        summary = fingerprint(parse_client_hello(openssl_hello()))
        self.assertFalse(summary["grease"])
        self.assertTrue(summary["ja4"].startswith("t13d"))
        self.assertEqual(summary["alpn"], "h2")
        self.assertTrue(summary["sni"])

    def test_partial_and_invalid_records(self):
        record = chrome_like_hello()
        self.assertIsNone(hello_length(record[:40]))
        self.assertEqual(hello_length(record + b"\x17\x03\x03"), len(record))
        with self.assertRaises(HelloError):
            hello_length(b"GET / HTTP/1.1\r\n")
        with self.assertRaises(HelloError):
            parse_client_hello(b"\x16\x03\x01\x00\x04\x02\x00\x00\x00")


class ForwardingTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        received = self.received

        class Upstream(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                received.append((list(self.headers.items()), body))
                payload = b'{"ok": true}'
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.upstream = HTTPServer(("127.0.0.1", 0), Upstream)
        self.certs = tempfile.TemporaryDirectory()
        cert_path, key_path = ensure_certificate(Path(self.certs.name))
        self.edge = EdgeServer(("127.0.0.1", 0), ("127.0.0.1", self.upstream.server_port), "edge-test-key",
                               cert_path, key_path)
        self.threads = [Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
                        for server in (self.upstream, self.edge)]
        for thread in self.threads:
            thread.start()

    def tearDown(self):
        for server in (self.edge, self.upstream):
            server.shutdown()
            server.server_close()
        self.certs.cleanup()

    def test_requests_are_forwarded_with_signed_tls_metadata(self):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection("127.0.0.1", self.edge.server_address[1], context=context, timeout=10)
        try:
            for attempt in range(2):
                connection.request("POST", "/api/v1/telemetry/", body=b'{"n": %d}' % attempt, headers={
                    "Content-Type": "application/json", "X-HumanProof-Edge": "spoofed",
                    "X-HumanProof-Edge-Signature": "spoofed"})
                response = connection.getresponse()
                self.assertEqual(response.status, 201)
                self.assertEqual(json.loads(response.read()), {"ok": True})
        finally:
            connection.close()
        self.assertEqual(len(self.received), 2)
        for attempt, (headers, body) in enumerate(self.received):
            self.assertEqual(body, b'{"n": %d}' % attempt)
            edge_values = [value for name, value in headers if name.lower() == "x-humanproof-edge"]
            signatures = [value for name, value in headers if name.lower() == "x-humanproof-edge-signature"]
            self.assertEqual(len(edge_values), 1)
            self.assertNotEqual(edge_values[0], "spoofed")
            expected = hmac.new(b"edge-test-key", edge_values[0].encode(), hashlib.sha256).hexdigest()
            self.assertEqual(signatures, [expected])
            metadata = json.loads(base64.urlsafe_b64decode(edge_values[0]))
            self.assertTrue(metadata["ja4"].startswith("t1"))
            self.assertEqual(metadata["requests_on_connection"], attempt + 1)
            self.assertGreaterEqual(metadata["handshake_ms"], 0)
            self.assertEqual(metadata["alpn"], None)
            names = [name.lower() for name, _ in headers]
            self.assertLess(names.index("content-type"), names.index("x-humanproof-edge"))


if __name__ == "__main__":
    unittest.main()
