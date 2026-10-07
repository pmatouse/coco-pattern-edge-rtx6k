"""Cross-check the guest EVP decoder against the producer's AESGCM library."""
import ctypes.util
import importlib.util
from pathlib import Path
import sys
import unittest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ROOT = Path(__file__).resolve().parents[2]
if sys.platform == "darwin":
    # macOS's system libcrypto stub aborts on load. Use Homebrew OpenSSL for this test.
    openssl = Path("/opt/homebrew/opt/openssl@3/lib/libcrypto.dylib")
    if not openssl.exists():
        raise SystemExit("Run on Linux with OpenSSL, or install Homebrew OpenSSL 3")
    ctypes.util.find_library = lambda name: str(openssl)

spec = importlib.util.spec_from_file_location(
    "guest", ROOT / "charts/coco-supported/gpu-workload/files/encrypted-file-test/guest.py"
)
guest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guest)


class CryptoTests(unittest.TestCase):
    def test_authenticated_decryption_and_rejections(self):
        # Public test key only; never the lab Trustee key.
        key = bytes(range(32))
        aad = b"edge-encrypted-gpu-v1|4096"
        for index, size in enumerate([0, 1, 16, 32772]):
            nonce = index.to_bytes(12, "little")
            plaintext = bytes(i % 251 for i in range(size))
            ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
            with self.subTest(size=size, case="valid"):
                self.assertEqual(guest.decrypt_gcm(key, nonce, ciphertext, aad), plaintext)
            for label, k, c, a in [
                ("wrong key", bytes([key[0] ^ 1]) + key[1:], ciphertext, aad),
                ("tampered tag", key, ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]), aad),
                ("wrong metadata", key, ciphertext, b"wrong"),
            ]:
                with self.subTest(size=size, case=label):
                    with self.assertRaises(guest.AuthenticationFailed):
                        guest.decrypt_gcm(k, nonce, c, a)


if __name__ == "__main__":
    unittest.main()
