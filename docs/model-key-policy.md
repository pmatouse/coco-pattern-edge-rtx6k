# GPU-gated model key on edge-rtx6k

This extends the CPU/GPU baseline validated on the Dell PowerEdge XE7745, node
9c-63-c0-dd-06-52, OpenShift 4.22.15. It adds an explicit model-key authorization
rule to Trustee 1.2.0. The VM still boots before attestation; the gate controls
secret release.

## Protected resource

- KBS/CDH resource: default/model-keys/edge-model-key
- Vault KV path: secret/hub/modelKeys
- Vault field and Kubernetes Secret data key: edge-model-key
- Kubernetes Secret: trustee-operator-system/model-keys
- ExternalSecret: model-keys-eso
- Material: 256 random bits, stored and delivered as 64 hexadecimal characters.
  A model encryption/decryption consumer must hex-decode this to obtain 32 bytes.
- No key value, Vault token or private credential is committed to Git or printed
  by the provided tests. The validation endpoint contains only hashes/status.

The policy protects every tag in default/model-keys, not only this one filename.
Other repositories using the reserved type model-keys are denied. Existing
kbsres1, registry credentials and other CPU bootstrap/demo resources keep CPU-only
support.

## Policy requirements

The exact policy is charts/hub/trustee-edge/files/resource-policy.rego.

For model keys, all of the following must hold:

1. KBS has verified the attestation token; unsigned caller-supplied claims cannot
   authorize a request.
2. Both cpu0 and gpu0 submodules exist in the same token. Every supplied submodule
   has affirming status and numeric hardware, executables and configuration
   trust claims in the affirming range 2–31. Missing or malformed fields fail
   closed, as do extra failing submodules.
3. cpu0 contains SNP evidence with debug and migration disabled and a launch
   measurement. The attestation-service policy independently checks the launch
   measurement, initdata and TCB against the existing approved references.
4. gpu0 contains NVIDIA evidence with overall attestation true, secure boot true,
   debug disabled and successful measurements. The existing NVIDIA verifier and
   GPU appraisal policy check report authenticity and driver/VBIOS RIMs.

The resource name is supplied by KBS as canonical request metadata
(data.plugin and data["resource-path"]), not taken from guest assertions.

This is a hardware/runtime attestation gate, not an image identity allowlist.
The lab's image acceptance policy remains insecureAcceptAnything, and Trustee
and Vault remain co-located on the cluster. No model weights have yet been
encrypted with this key and no inference server has been deployed.

## Versioned source and GitOps

The private Trustee chart fork is charts/hub/trustee-edge, version
0.10.3-edge.1, released under tag edge-trustee-0.10.3-1. Its base is the recorded
Validated Patterns trustee chart 0.10.2. The chart version and resource policy
are the changes; its other templates, including GPU and SNP appraisal, remain
from that base.

The AMD GPU profile consumes this released copy through the pattern Git branch
as a single-source Argo application. It uses path charts/hub/trustee-edge,
chart: null, chartVersion: null, and the private pattern repo URL. Argo rejects
two different revisions of the same Git repository in a multi-source app, so
the first attempted tag/values split was corrected. The policy chart's tracked
content still matches its release tag:

    git diff edge-trustee-0.10.3-1 HEAD -- charts/hub/trustee-edge

The policy is GitOps-managed; do not manually edit the live ConfigMap as the
permanent configuration. No admin bypass was enabled in KBS.

## Recreate after the CPU/GPU baseline

Start from the documented healthy CPU/GPU baseline with 64 measured reference
values, GPU Operator 26.3.0 and the single-GPU verifier passing. Use the protected
administrator kubeconfig through KUBECONFIG. Retain the exact deployed Git
revision in addition to the chart release tag.

The implementation was staged so no model key was mounted in KBS before the
strict policy was verified:

1. Release the policy chart and tests: commit de1a4dd1fa579e976222a67f5b3023d0c15ff235.
2. Configure its deployment: commits 694a8ebb60f66729e99f1ef4577eded01afd4b47 and
   f1b051b (the latter fixes Argo's same-repository revision restriction).
3. Create/retain the key in initialized Vault:
   python3 scripts/provision-model-key.py
4. Confirm the strict resource-policy ConfigMap and running KBS use the new
   policy. A new KBS pod was rolled out by the operator during this change.
   Run the CPU-only denial test before enabling the resource.
5. Enable the model-key ExternalSecret and GPU test retrieval using commit
   3a7e85e10a5ee76803ea60537ad4e9b93693063b.
6. Run all live positive and negative checks below.

When replaying into another private fork, keep your existing repository and
branch values. Change the Trustee repoURL in the GPU override to your fork.
Preserve the released chart content and push the release tag along with the
rebuild branch; do not force-reset the existing deployment branch.

For a clean installation using the final source tree directly, run the
provision-model-key helper as soon as Vault has initialized and its administrative
Secret exists. The final GPU profile expects model-keys-eso to populate the
model-keys Secret before KBS can mount it. Missing key material fails closed; it
must not be worked around by removing policy checks.

The helper creates the Vault entry with CAS=0 only when absent. If an entry
already exists, it retains it without rotation. It receives the Vault token
through process stdin/in-memory data and never prints it. Key rotation and
model re-encryption are separate operations.

The GPU profile's extraValueFiles must include, in order:

    /overrides/values-trustee.yaml
    /overrides/values-snp-vcek.yaml
    /overrides/values-trustee-gpu-model-keys.yaml

The last file retains kbsres1 and passphrase and adds model-keys. Helm replaces
arrays, so do not drop the previous entries.

Allow the root application's normal 120-second Vault hook to finish. Check:

    oc get applications.argoproj.io -n vp-gitops
    oc get externalsecret model-keys-eso -n trustee-operator-system
    oc get kbsconfig -n trustee-operator-system
    oc get pods -n trustee-operator-system
    oc get pods -n gpu-workload

Never inspect the model-keys Secret with a command that prints its data.

## Tests

### Policy cases

Use OPA 1.21.1 (the implementation run verified the official binary's published
SHA-256) and run:

    OPA=/path/to/opa python3 tests/model-key-policy/test_policy.py

The 68 cases cover valid combined evidence, missing CPU/GPU modules, invalid and
missing trust-vector fields, invalid NVIDIA claims, CPU masquerading as a GPU,
debug-enabled CPU evidence, extra failing devices, protected resource variants,
malformed request metadata and retained CPU-only demo access.

These are policy-level fixtures, not fabricated successful hardware reports.

### Live checks

    python3 scripts/test-model-key-live.py --ingress-ip 10.14.202.14

The script:

- Confirms the GPU guest completed CUDA and retrieved the exact configured model
  key by comparing its hash in memory.
- Requests that same existing key from the CPU-only SNP demo. It requires both a
  failed CDH response and fresh KBS PolicyDeny/HTTP 401 logs. CDH exposes this as
  HTTP 500, so the CDH status alone is not treated as proof.
- Sends an unsigned JWT claiming CPU/GPU appraisal and requires rejection.
- Completes a real NVIDIA authentication handshake, then submits a deliberately
  invalid Blackwell report/certificate. It requires attestation rejection.
- Attempts key retrieval in that failed-attestation session and requires HTTP
  401/403. No successful token or secret value is printed.

For policy staging before exposing the key, use --negative-only. For the final
acceptance run, do not use that option.

The invalid-evidence test uses synthetic bad input to the real verifier. It
does not damage a GPU, flash firmware, change CC mode or modify trusted roots.

Read the nonsecret GPU result through the authenticated Kubernetes API:

    oc get --raw /api/v1/namespaces/gpu-workload/services/gpu-validation:8080/proxy/status.txt

Expect GPU_MODEL_KEY_BEGIN, a SHA-256 line and GPU_MODEL_KEY_PASS, in addition to
the CUDA and original test-secret markers. The original key3 and model-key
hashes are different. The readiness marker is created only after all checks
succeed. Arbitrary exec and log streaming remain blocked in this secure VM.

To re-execute the GPU workload:

    oc delete pod -n gpu-workload -l app=gpu-vectoradd
    oc get pods -n gpu-workload -w

Wait for 2/2 Ready, then run the live tests. The first image pull can take time.

Recheck CPU secret delivery, secure exec denial, GPU exec denial, cluster
operators, Kata readiness and all 16 Argo applications. The earlier baseline
validation scripts remain available in the CPU/GPU rebuild bundle.

## Recovery and limitations

If KBS cannot start after mounting model-keys, check Vault and model-keys-eso.
If a CPU-only request returns a missing-resource error instead of PolicyDeny,
the test has not demonstrated the gate; check the actual loaded policy and KBS
logs. If the GPU fails, check composite cpu0/gpu0 appraisal, firmware references,
NRAS connectivity and the new key Secret without printing its contents.

Do not roll back to a permissive resource policy while leaving model keys
mounted. Remove the protected key from KBS first if deliberately retiring this
feature. Keep Vault recovery material in a separate secret store.

The clean-cluster procedure has not been destructively rerun. The live tests
validate the current one-GPU lab configuration, not all four cards individually
or a production inference deployment.

Policy concepts and canonical request fields:
https://confidentialcontainers.org/docs/attestation/policies/
NVIDIA verifier source:
https://github.com/confidential-containers/trustee/tree/main/deps/verifier/src/nvidia
