"""Model loading requires CURRENT encryption, not just a pending configuration."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / 'charts/coco-supported/encrypted-inference/files/gpu_security.py'
spec = importlib.util.spec_from_file_location('gpu_security', SOURCE)
security = importlib.util.module_from_spec(spec)
spec.loader.exec_module(security)


class EncryptionGateTest(unittest.TestCase):
    def test_current_state_controls_gate(self):
        for state in ('Enabled', 'Disabled', 'N/A', ''):
            with self.subTest(current=state), tempfile.TemporaryDirectory() as directory:
                xml = ('<nvidia_smi_log><gpu><uuid>test-gpu</uuid><dram_encryption_mode>'
                       '<current_dram_encryption>' + state + '</current_dram_encryption>'
                       '<pending_dram_encryption>Enabled</pending_dram_encryption>'
                       '</dram_encryption_mode></gpu></nvidia_smi_log>')
                reply = {'returncode': 0, 'stdout': xml, 'stderr': ''}
                with patch.object(security, 'RESULTS', Path(directory)), patch.object(security, 'query', return_value=reply):
                    if state == 'Enabled':
                        self.assertEqual(security.require_encrypted_dram()['dram_encryption_current'], ['Enabled'])
                    else:
                        with self.assertRaises(RuntimeError):
                            security.require_encrypted_dram()
                    self.assertTrue((Path(directory)/'gpu-security-before-model.json').exists())

    def test_failed_query_denies(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(security, 'RESULTS', Path(directory)), patch.object(security, 'query', return_value={
                    'returncode': 1, 'stdout': '', 'stderr': 'driver unavailable'}):
                with self.assertRaises(RuntimeError):
                    security.require_encrypted_dram()


if __name__ == '__main__':
    unittest.main()
