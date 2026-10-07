#!/usr/bin/env python3
"""Validate the encrypted-input GPU test and CPU-only key denial without exposing data."""
import argparse
import base64
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess


OC = ["oc", "--request-timeout=30s"]


def run(args, check=True):
    result = subprocess.run(OC + args, capture_output=True, timeout=75)
    if check and result.returncode:
        raise RuntimeError("Cluster command failed; response withheld")
    return result


def get(*args):
    return json.loads(run(["get", *args, "-o", "json"]).stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cm = get("configmap", "encrypted-gpu-test", "-n", "gpu-workload")
    bundle = json.loads(cm["data"]["bundle.json"])
    report = json.loads(run([
        "get", "--raw",
        "/api/v1/namespaces/gpu-workload/services/gpu-encrypted-file:8080/proxy/result.json"
    ]).stdout)
    expected_ciphertext = hashlib.sha256(base64.b64decode(bundle["ciphertext_b64"])).hexdigest()
    assert report["status"] == "PASS", "Guest test did not pass"
    assert report["count"] == bundle["count"], "Input count mismatch"
    assert report["ciphertext_sha256"] == expected_ciphertext, "Stale or mismatched ciphertext"
    assert report["output_sha256"] == bundle["expected_output_sha256"], "GPU result mismatch"
    assert report["negative_tests"] == {
        "wrong_key_rejected": True, "tampered_ciphertext_rejected": True
    }, "Missing negative authentication tests"
    pods = get("pods", "-n", "gpu-workload", "-l", "app=gpu-encrypted-file")["items"]
    pods = [p for p in pods if not p["metadata"].get("deletionTimestamp")]
    assert len(pods) == 1, "Expected one test VM"
    pod = pods[0]
    assert pod["spec"]["runtimeClassName"] == "kata-cc-nvidia-gpu"
    assert pod["spec"]["containers"][0]["resources"]["limits"]["nvidia.com/pgpu"] == "1"
    assert any(c["type"] == "Ready" and c["status"] == "True" for c in pod["status"]["conditions"])
    assert not any("secret" in v for v in pod["spec"]["volumes"]), "Unexpected Kubernetes Secret mount"
    print("PASS: pre-encrypted input decrypted in GPU CVM; CUDA output matches producer digest")
    print("PASS: wrong key and modified ciphertext rejected by AES-GCM")

    result = run(["exec", "-n", "gpu-workload", pod["metadata"]["name"],
                  "-c", "encrypted-gpu-test", "--", "/bin/true"], check=False)
    assert result.returncode and b"ExecProcessRequest is blocked by policy" in result.stderr
    print("PASS: arbitrary exec into the GPU CVM remains denied")

    cpu = get("deployment", "insecure-policy", "-n", "hello-openshift")
    assert cpu["spec"]["template"]["spec"]["runtimeClassName"] == "kata-cc"
    started = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    result = run([
        "exec", "-n", "hello-openshift", "deployment/insecure-policy", "--", "curl",
        "--silent", "--show-error", "--max-time", "45", "--write-out", "\n%{http_code}",
        "http://127.0.0.1:8006/cdh/resource/default/model-keys/edge-model-key"
    ])
    _, status = result.stdout.rsplit(b"\n", 1)
    assert status in [b"401", b"403", b"500"], "CPU-only request was not denied"
    logs = run(["logs", "-n", "trustee-operator-system", "deployment/trustee-deployment",
                "-c", "kbs", "--since-time=" + started]).stdout.decode()
    logs = re.sub(r"\x1b\[[0-9;]*m", "", logs)
    assert "PolicyDeny" in logs, "Missing KBS policy denial"
    assert re.search(r'GET /kbs/v0/resource/default/model-keys/edge-model-key HTTP/1.1" 401', logs)
    print("PASS: CPU-only SNP guest denied the decryption key (KBS PolicyDeny / HTTP 401)")
    record = {
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "guest": report, "pod": pod["metadata"]["name"],
        "runtime": pod["spec"]["runtimeClassName"], "gpu_count": 1,
        "secure_exec_denied": True, "cpu_only_key_denied": True,
        "kbs_denial_http_status": 401, "cdh_denial_http_status": int(status),
        "limits": ["Lab key and co-located Trustee; no administrator isolation claim",
                   "Permissive image/workload acceptance remains; not production hardening",
                   "Decryption executes on the guest CPU; decrypted input is then processed by CUDA"],
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
