# GPU extension validation — 7 October 2026

> **DRAFT ENGINEERING RUNBOOK / LAB RECORD — NOT PRODUCTION READY.**
> Historical GPU-extension snapshot at `877791d`, recorded 7 October 2026. The staged installation was tested; the complete clean-cluster rebuild procedure has not been rerun. These guides have not completed a handoff review. Read the [documentation status and sequence](README.md) first. The later [model-key policy phase](../model-key-policy.md) supersedes statements here that GPU-required key release remains future work. Do not roll back the policy on an existing cluster while protected model keys remain mounted.

## Passed on edge-rtx6k

- GPU Operator CSV `gpu-operator-certified.v26.3.0` Succeeded; ClusterPolicy Ready.
- CC Manager changed all four GPUs from off to on and reported ready. VFIO and the Kata sandbox device plugin exposed four allocatable `nvidia.com/pgpu` resources.
- One RTX PRO 6000 Blackwell Server Edition was assigned to an AMD SNP VM using `kata-cc-nvidia-gpu`, one vCPU and 32 GiB memory.
- GPU UUID `GPU-365e7189-b4ff-8aa9-a0d0-3c3a2589c32b` reported VBIOS **98.02.8D.00.01**, driver **595.58.03**.
- CUDA vectorAdd completed 50,000 elements and reported `Test PASSED`.
- The same guest fetched `default/kbsres1/key3`. Its SHA-256 matched the configured Kubernetes Secret in memory; no secret value was printed.
- Trustee recorded **cpu0 and gpu0 Affirming in the same composite appraisal**. NVIDIA endorsement verification passed, overall result was true, secure boot true, debug disabled, and driver/VBIOS measurements successful. The resource request returned HTTP 200.
- Arbitrary exec into the GPU guest failed with `ExecProcessRequest is blocked by policy`. Secure log streaming stayed disabled.
- All **16** Argo applications were Synced/Healthy. Cluster operators were Available and not Degraded.
- CPU regression checks passed: Kata ready, debug exec allowed, secure exec denied, and real KBS test-secret retrieval unchanged.
- The corrected release collector independently reproduced **64** references: 32 CPU plus 32 GPU. It checked the release artifact hashes before computing them. CPU timeout is 6; GPU timeout is 15.
- Helm rendered the changed workload, and server-side dry runs accepted its resources. Python helper syntax checks passed; both validation helpers were exercised successfully against the deployment.
- Temporary collector Job, ConfigMap and copied registry-auth Secret were deleted. Temporary node-side GPU inspection and measurement tooling was removed.

The exact deployment revision, ordered commits, operand image IDs and application state are in [deployment-versions.json](deployment-versions.json). Nonsecret evidence is in [reference/gpu-validation-result.txt](reference/gpu-validation-result.txt), [reference/attestation-summary.json](reference/attestation-summary.json), and [reference/collector-validation.log](reference/collector-validation.log).

## Corrections needed during deployment

1. Preserved AMD-only configuration and all SNP/bare-metal/GPU Trustee overrides; disabled redundant IOMMU MachineConfigs.
2. Set CC Manager's `OPERATOR_NAMESPACE` explicitly to its actual namespace, resolving incorrect-namespace RBAC failures.
3. Corrected SNP GPU measurement inputs to the running runtime's `agent.launch_process_timeout=15`.
4. Preserved secure `ReadStreamRequest=false`. Empty `oc logs` was expected, so the test now exposes a nonsecret result document through an internal service.
5. Prepared encrypted guest result-volume permissions with a root initialization container. The verifier and result server run as UID 1001; no host mounts or privileged containers were added. A namespace-scoped Role allows the dedicated service account to use `anyuid`.
6. Added a readiness marker created only after successful CUDA execution and test-secret retrieval, preventing an idle Running container from being mistaken for a passing test.

## Limits and next work

- This validates **one GPU in one confidential VM**, not all four cards individually or multiple GPUs in one VM. CC mode/resource readiness was checked for all four; the detailed VBIOS and CUDA result belongs to the tested card.
- No firmware was flashed and no node reboot was needed for this GPU extension. The earlier CPU bootstrap did require a Kata-related node reboot.
- A clean-cluster teardown/rebuild was **not** performed to test the combined runbook. The package records the successfully executed staged installation and supplies replay steps.
- The exact server/CPU/GPU combination remains a lab validation; vendor support coverage was not established beyond the documentation discussed in the runbook.
- The shared KBS policy permits CPU-only access for existing CPU examples. It evaluates submitted GPU evidence, but does not require a GPU for every resource. **A dedicated model-key policy requiring GPU evidence, with missing/invalid GPU evidence negative tests, remains to be implemented before confidential inference.** No such negative test is claimed here.
- No model server, model weights, encrypted model loading, inference performance test, or production hardening was deployed in this phase.
- Image acceptance policy remains the baseline's `insecureAcceptAnything`. Trustee/Vault are co-located on the lab cluster. See the CPU runbook for these trust and recovery limits.
- This is a source/reference rebuild package, not a complete disconnected image mirror or a byte-for-byte lock of every inherited chart/operator.

No Slack messages were sent.
