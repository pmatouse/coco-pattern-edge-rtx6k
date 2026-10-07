#!/usr/bin/env python3
"""Pre-encrypt random demo vectors on a trusted client; write ciphertext only."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import secrets
import struct
import subprocess

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=4096)
    args = parser.parse_args()
    if not 1 <= args.count <= 16384:
        parser.error("count must be between 1 and 16384")
    # Lab producer is trusted and has administrator access. This does not model
    # secrecy from that administrator; production producers use their own KMS.
    result = subprocess.run([
        "oc", "--request-timeout=30s", "get", "secret", "model-keys",
        "-n", "trustee-operator-system", "-o", "json"
    ], capture_output=True, check=True, timeout=40)
    secret = json.loads(result.stdout)
    key = bytes.fromhex(base64.b64decode(secret["data"]["edge-model-key"]).decode("ascii"))
    if len(key) != 32:
        raise ValueError("Expected 256-bit key")
    count = args.count
    a = [secrets.randbelow(1000000) for _ in range(count)]
    b = [secrets.randbelow(1000000) for _ in range(count)]
    fmt = "<" + "I" * count
    plaintext = struct.pack("<I", count) + struct.pack(fmt, *a) + struct.pack(fmt, *b)
    expected = struct.pack(fmt, *[x + y for x, y in zip(a, b)])
    nonce = secrets.token_bytes(12)
    aad = ("edge-encrypted-gpu-v1|" + str(count)).encode()
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    if AESGCM(key).decrypt(nonce, ciphertext, aad) != plaintext:
        raise RuntimeError("Producer roundtrip failed")
    bundle = {
        "format": "edge-encrypted-gpu-v1", "algorithm": "AES-256-GCM",
        "resource": "default/model-keys/edge-model-key", "count": count,
        "nonce_b64": base64.b64encode(nonce).decode(),
        "ciphertext_b64": base64.b64encode(ciphertext).decode(),
        "expected_output_sha256": hashlib.sha256(expected).hexdigest(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle, indent=2) + "\n")
    print("Prepared AES-256-GCM ciphertext for " + str(count) + " vector pairs; key and plaintext withheld")
    print("Ciphertext SHA-256: " + hashlib.sha256(ciphertext).hexdigest())


if __name__ == "__main__":
    main()
