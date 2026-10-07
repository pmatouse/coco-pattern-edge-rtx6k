"""Cross-check streaming OpenSSL against independent cryptography AESGCM."""
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'charts/coco-supported/encrypted-inference/files'))
from artifact import crypt_stream  # noqa: E402

if sys.platform == 'darwin':
    library = Path('/opt/homebrew/opt/openssl@3/lib/libcrypto.3.dylib')
    if library.exists():
        os.environ['TEST_LIBCRYPTO'] = str(library)


class StreamingCrypto(unittest.TestCase):
    def test_vectors_and_rejections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key, aad = os.urandom(32), b'qwen-authenticated-metadata'
            for size in (0, 1, 16, 1048593):
                nonce, data = os.urandom(12), os.urandom(size)
                expected = AESGCM(key).encrypt(nonce, data, aad)
                info = crypt_stream(io.BytesIO(data), root/'encrypted', key, nonce, aad)
                self.assertEqual((root/'encrypted').read_bytes(), expected[:-16])
                self.assertEqual(info['tag'], expected[-16:].hex())
                crypt_stream(io.BytesIO(expected[:-16]), root/'plain', key, nonce, aad, expected[-16:])
                self.assertEqual((root/'plain').read_bytes(), data)
                for trial_key, trial_aad, trial_tag in (
                    (bytes([key[0] ^ 1]) + key[1:], aad, expected[-16:]),
                    (key, b'wrong-model-identity', expected[-16:]),
                    (key, aad, bytes([expected[-16] ^ 1]) + expected[-15:]),
                ):
                    with self.assertRaises(ValueError):
                        crypt_stream(io.BytesIO(expected[:-16]), root/'plain', trial_key, nonce, trial_aad, trial_tag)
                    self.assertFalse((root/'plain').exists())


if __name__ == '__main__':
    unittest.main()
