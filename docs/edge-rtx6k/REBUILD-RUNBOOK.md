# Recreate edge-rtx6k: CPU baseline and confidential GPU extension

> **DRAFT ENGINEERING RUNBOOK / LAB RECORD — NOT PRODUCTION READY.**
> Historical GPU-extension snapshot at `877791d`, recorded 7 October 2026. The staged installation was tested; the complete clean-cluster rebuild procedure has not been rerun. These guides have not completed a handoff review. Read the [documentation status and sequence](README.md) first. The later [model-key policy phase](../model-key-policy.md) supersedes statements here that GPU-required key release remains future work. Do not roll back the policy on an existing cluster while protected model keys remain mounted.

Recorded 7 October 2026. Start with a clean, healthy OpenShift **4.22.15** cluster on the same Dell PowerEdge XE7745. This package includes the detailed CPU bootstrap procedure and the GPU changes made afterward. The clean-cluster sequence has not been rerun destructively. Consult `VALIDATION-RECORD.md` for what actually passed and what remains open.

No credentials are embedded in this package. Obtain a fresh administrator kubeconfig, entitled registry credentials, and read access to the private Git repository. Generate new test secrets or restore them separately. Firmware references and the public VCEK certificate are not authentication credentials.

## 1. Recreate the CPU baseline first

Follow [the included CPU runbook](cpu-baseline/REBUILD-RUNBOOK.md) in full. It documents access, DNS, exact disk selection, private Git credentials, installer preparation, VCEK collection, Vault initialization, operator approvals, the Kata reboot, and acceptance tests.

Set its `KIT` variable to this package's **`cpu-baseline` subdirectory**. Use the recorded CPU source commit `5f158c30f120f9eb041d5d77a81fedb916684bb3`, not the current head of the deployment branch. The shared branch has advanced to the GPU configuration. For a new clean rebuild, create a new branch at the CPU commit in a repository you control and set the repository/branch fields as explained in the CPU runbook. Do not reset or force-push the existing deployment branch.

Checkpoint: 14 applications Synced/Healthy; one Kata-ready node; CPU secret retrieval passes; debug exec succeeds; secure exec is denied. Do not proceed to GPUs while that baseline fails.

The only approved LVMS device remains:

```text
/dev/disk/by-id/nvme-eui.01000000000000058ce38ee3034e25da
serial 8E80A0D804M3
```

The OS disk serial `CN0CMFVPFCP0054700ZL` and the other data drives remain excluded. Reinstalling OpenShift does not automatically clear the LVMS/Vault disk. The CPU procedure includes the necessary inspection and recovery boundary.

## 2. Hardware and stack

| Component | Recorded configuration |
| --- | --- |
| Server | Dell PowerEdge XE7745, BIOS 1.7.6 |
| CPUs | Two AMD EPYC 9555, SEV-SNP enabled |
| GPUs | Four RTX PRO 6000 Blackwell Server Edition, PCI `10de:2bb5` |
| GPU addresses | `0000:42:00.0`, `0000:8c:00.0`, `0001:04:00.0`, `0001:c8:00.0` |
| IOMMU groups | 81, 45, 145, 109 respectively; one GPU per group |
| OpenShift / OSC / Trustee | 4.22.15 / 1.13.0 / 1.2.0 |
| GPU Operator | **26.3.0**, Manual approval, channel `v26.3` |
| CC Manager | 0.3.0 |
| GPU runtime | `kata-cc-nvidia-gpu`, handler `kata-snp-nvidia-gpu` |
| Guest driver | 595.58.03 from the OSC confidential GPU initrd |
| First guest | One GPU, one vCPU, 32768 MiB memory |

The host uses VFIO; OSC supplies Kata and the guest driver. Do not install an ordinary host NVIDIA compute-driver stack or a second Kata manager. The four cards were changed from CC off to CC on by CC Manager, with GPU resets. No node reboot or GPU firmware flash was needed in this extension. CC configuration is node-wide even though the first workload allocates only one card.

This is a lab validation of the specific combination. NVIDIA documents single-GPU confidential passthrough for this model; the Red Hat OSC matrix does not explicitly name this Dell/Turin/RTX combination. Four cards on the host do not establish four-GPU support in one confidential VM.

## 3. Prepare the GPU source in the rebuild branch

Use the same `REPO`, `DEPLOY_BRANCH`, `KUBECONFIG`, and protected `SECURE` directory from the CPU procedure. Set:

```bash
export GPU_KIT=/absolute/path/to/coco-pattern/docs/edge-rtx6k
git -C "$REPO" status --short
git -C "$REPO" fetch origin
```

The package's `deployment-versions.json` records the final source revision. The source archive `coco-pattern-gpu.tar.gz` contains its tracked files without credentials. Argo still needs a reachable Git remote; extracting an archive alone does not change the cluster.

For a rebuild branch descended from the CPU commit, apply the recorded GPU changes in order. Preserve your branch/repository fields if they differ from this installation. Resolve a conflict by reviewing both sides; do not overwrite unrelated rebuild settings.

```bash
git -C "$REPO" cherry-pick b3e19098ff408b86b34f5ded530115e4b2fca345
git -C "$REPO" cherry-pick 4fa16a414e6efd9ebbb20b35e61870e310778b0a
git -C "$REPO" push origin "$DEPLOY_BRANCH"
```

These first two changes enable the operator and Trustee GPU verification but leave the GPU workload disabled. They also repair the CC Manager namespace.

Key settings to inspect before pushing:

- `values-global.yaml`: hardware profile `amd-snp-gpu`; existing CPU runtime remains `kata-cc`; attestation bypass remains false.
- AMD GPU override: disable Intel DCAP/device-plugin applications and `tdx.enabled`; enable all three Trustee entries `kbs.snp.enabled`, `kbs.baremetal.enabled`, `kbs.gpu.enabled`. Helm replaces arrays, so retain the complete list.
- GPU subscription: `gpu-operator-certified.v26.3.0`, `certified-operators`, namespace `nvidia-gpu-operator`, Manual approval.
- NVIDIA chart: `iommu.enabled=false` on this already-configured host; no redundant IOMMU MachineConfig.
- ClusterPolicy: CC Manager enabled/default `on`, VFIO and Kata sandbox device plugin enabled; host driver, toolkit, ordinary device plugin, and NVIDIA Kata Manager disabled.
- CC Manager environment: **`OPERATOR_NAMESPACE=nvidia-gpu-operator`**. Without this, version 0.3.0 tries to list pods in `gpu-operator` and fails RBAC. Fix the namespace rather than granting broader access.

After switching the hardware profile, make the Patterns controller recompute the root application's value-file list:

```bash
oc annotate pattern coco-pattern -n patterns-operator \
  codex.redhat.com/config-refresh="$(git -C "$REPO" rev-parse --short HEAD)" --overwrite
oc annotate applications.argoproj.io coco-pattern-baremetal -n vp-gitops \
  argocd.argoproj.io/refresh=hard --overwrite
oc get applications.argoproj.io coco-pattern-baremetal -n vp-gitops \
  -o jsonpath='{.spec.sources[1].helm.valueFiles}'
```

The effective list must contain `$patternref/overrides/values-hw-amd-snp-gpu.yaml`. The normal `wait-for-vault-unsealed` sync hook includes a 120-second wait; allow it to complete.

## 4. Approve the exact GPU Operator plan

```bash
python3 "$GPU_KIT/scripts/approve-pinned-plans.py"
python3 "$GPU_KIT/scripts/approve-pinned-plans.py" --apply
oc get csv -n nvidia-gpu-operator
oc get clusterpolicies.nvidia.com gpu-cluster-policy
oc get pods -n nvidia-gpu-operator
```

The helper accepts only the recorded OSC, Trustee, and GPU CSVs in their matching namespaces. The GPU plan must contain only `gpu-operator-certified.v26.3.0`. A newer channel head is not interchangeable; later upgrade plans remain unapproved.

Check node state:

```bash
oc get nodes -o custom-columns=NAME:.metadata.name,CC:.metadata.labels.nvidia\\.com/cc\\.mode\\.state,READY:.metadata.labels.nvidia\\.com/cc\\.ready\\.state,PGPU:.status.allocatable.nvidia\\.com/pgpu
oc get nodefeaturerules -A
```

Expected: CC `on`, ready `true`, allocatable `pgpu=4`; ClusterPolicy Ready; operator, CC Manager, VFIO Manager, sandbox validator and Kata device plugin Ready. NFD already had `openshift-nfd/consolidated-hardware-features` before this installation.

Discover the CC Manager pod by listing pods rather than assuming an `app=nvidia-cc-manager` selector. This version's image is distroless and has no shell. Read its logs through `oc logs`; do not expect `oc exec ... sh` to work.

## 5. Calculate and load CPU plus GPU references

Before enabling the workload, obtain the corrected collector from this package. Use the installer container configured in the CPU procedure, with its entitled registry authentication already at `/pattern-home/pull-secret.json`:

```bash
podman cp "$GPU_KIT/scripts/collect-edge-cpu-gpu-reference-values.py" \
  coco-rebuild:/pattern-home/collect-edge-cpu-gpu-reference-values.py
podman exec coco-rebuild python3 -m pip install \
  'osc-veritas[snp]==0.1.3rc1' 'sev-snp-measure==0.0.13'
podman exec coco-rebuild python3 /pattern-home/collect-edge-cpu-gpu-reference-values.py
podman cp coco-rebuild:/pattern-home/.coco-pattern/firmware-reference-values.json \
  "$SECURE/firmware-reference-values.json"
python3 "$GPU_KIT/cpu-baseline/scripts/load-reference-values.py" \
  "$SECURE/firmware-reference-values.json"
```

Expected: **64** measurements, consisting of 32 CPU and 32 confidential GPU variants for 1–32 vCPUs. The nonconfidential GPU initrd is excluded. Keep the CPU entries so existing workloads continue attesting.

Critical command-line difference, verified from running QEMU:

```text
CPU: agent.launch_process_timeout=6 cgroup_no_v1=all systemd.unified_cgroup_hierarchy=1
GPU: agent.launch_process_timeout=15 cgroup_no_v1=all pci=realloc pci=nocrs pci=assign-busses nvrc.smi.srs=1
```

The initial GPU calculation mistakenly retained 6; the supplied corrected script uses 15. Do not allowlist an observed failing report instead of calculating from trusted artifacts and the actual runtime configuration.

The collector checks these release artifact hashes:

| Artifact | SHA-256 |
| --- | --- |
| OVMF SNP | `1aa196fe94e56809aa668fc4eabfc50923131288e018bcf0c6ab4dd3581bd850` |
| Guest kernel | `0317da8a23853124ed4e4da6c56254f40a741ff4507e1afbbcb7f32e5c097206` |
| CPU initrd | `1f9b3a90c60ec7a94985ae966fc66ecb4d04055ca3471b9f0074068f4dac8b62` |
| Confidential GPU initrd | `abd755287282a268578687137bf437caeda6139c1a654f18d13f8a72a68892ca` |

TCB arrays remain bootloader 3, TEE 2, SNP 6, microcode 117. Changes in release, BIOS/firmware, CPU model, kernel command line or images require reviewed recollection. `reference/collector-validation.log` records an independent release-image calculation matching all 64 supplied entries after the timeout correction.

Verify the published reference count as shown in the CPU runbook; it must now be 64. `scripts/calculate-installed-gpu-snp.py` is a fallback that checks installed node artifacts against the same hashes before calculating 32 GPU entries. It emits only GPU references: do not replace the combined file with its output.

## 6. Enable and validate the first GPU workload

After the operator, CC state and references pass, apply the later fixes to your rebuild branch, then push once:

```bash
git -C "$REPO" cherry-pick 381c077b426e575f2db6299c1e83fed7f1a12ddc
git -C "$REPO" cherry-pick 06d15818f060691d557ad740abec5b0ee41d4116
git -C "$REPO" cherry-pick 3f4b5905979c06d2b5f8c1c94bf9ef13f5ac277f
git -C "$REPO" cherry-pick 5ce102350fa670590d422f34282757d2f902b274
git -C "$REPO" cherry-pick 877791d35284e718e46d444146b69b93d2eb8aa2
git -C "$REPO" push origin "$DEPLOY_BRANCH"
oc annotate applications.argoproj.io coco-pattern-baremetal -n vp-gitops \
  argocd.argoproj.io/refresh=hard --overwrite
```

The recorded final source commit is `877791d35284e718e46d444146b69b93d2eb8aa2`. Cherry-picking produces different hashes on a customized rebuild branch; retain the resulting revision too.

The workload must use:

```yaml
runtimeClassName: kata-cc-nvidia-gpu
# Pod annotation:
# io.katacontainers.config.hypervisor.default_memory: "32768"
# coco.io/initdata-configmap: initdata
# Container resource limit:
# nvidia.com/pgpu: 1
```

The pinned verifier image is:

```text
quay.io/openshift_sandboxed_containers/gpu-verifier@sha256:dcc55b6c51a85cda62322f7f07f5aa8d42632481254d8bf47e2e24705b245797
```

The chart uses `Recreate`, secure initdata, fail-fast shell handling and bounded commands. A dedicated service account has a namespace-scoped permission to use the `anyuid` SCC solely so an initialization container can prepare result-volume ownership inside the confidential VM. The verifier and HTTP server run as UID 1001. No host mounts or privileged containers are added. This handles the encrypted guest volume permissions rather than relying on host-side fsGroup propagation. The image is approximately 7.6 GB; guest-side pulling occurs in addition to host image handling. A first pull takes time.

```bash
oc get applications.argoproj.io -n vp-gitops
oc get pods -n gpu-workload -o wide
oc get events -n gpu-workload --sort-by=.metadata.creationTimestamp
oc get --raw /api/v1/namespaces/gpu-workload/services/gpu-validation:8080/proxy/status.txt
python3 "$GPU_KIT/scripts/validate-gpu.py"
KUBECONFIG="$KUBECONFIG" python3 "$GPU_KIT/scripts/validate-cpu-baseline.py" \
  --expected-apps 16 --ingress-ip 10.14.202.14
```

A Running pod alone is insufficient. Require CUDA success, successful test-secret retrieval, and Trustee appraisal containing **both `cpu0` and `gpu0` as Affirming**. The secure policy blocks both arbitrary exec and stdout/stderr streaming (`ReadStreamRequest=false`), so `oc logs` is intentionally empty. The test writes a nonsecret report to an in-VM shared volume; a pinned UBI HTTP sidecar serves it through a ClusterIP service and the authenticated Kubernetes service-proxy API. No external Route is created. The sidecar readiness probe checks a marker created only after CUDA and secret retrieval succeed. The report includes pass markers only after commands succeed. Secret validation compares hashes; never paste the secret itself into logs or this package.

Trustee uses remote NVIDIA verification. Allow its HTTPS/DNS access to NVIDIA attestation and certificate/RIM services. Observed successful evidence included NVIDIA overall result true, secure boot true, debug disabled, valid report signatures, and successful driver/VBIOS RIM checks.

The shared resource policy retains CPU-only access for the CPU demos. It appraises every submitted submodule, but does not require a GPU for every resource. Before deploying protected model keys, add a resource-specific policy that explicitly requires GPU evidence, then test missing/invalid GPU evidence rejection. Generic `kbsres1/key3` access is not proof of that stronger model-key policy.

## 7. Diagnosis and recovery

| Symptom | Check/action |
| --- | --- |
| Root still uses CPU override | Annotate the Pattern CR and inspect the actual root value-file list. |
| CC Manager 403 in namespace `gpu-operator` | Set `OPERATOR_NAMESPACE` to the release namespace. Do not expand RBAC. |
| No `pgpu` allocation | Check CC readiness, VFIO binding, NFD rules and Kata sandbox device plugin logs. |
| CDH resource-provider failure | Inspect guest/Trustee evidence and reference values; compare actual GPU QEMU `-append`, particularly timeout 15. |
| Corrected references but old VM still fails | Confirm ACM published the new values; recreate only the test pod for a fresh attestation session. |
| Empty workload logs | Expected with secure stream policy. Read the internal status endpoint and require the success readiness marker. |
| `oc exec` denied | Expected for secure initdata. Do not weaken the policy just to run diagnostics. |
| Local disk/Podman failure | Use a Linux administration host or a controlled in-cluster collector. Do not pull large release images onto a nearly full workstation. |

The CPU runbook documents recovery of Vault and initdata after a node reboot. Changes to GPU mode can reset devices; stop GPU workloads before intentionally changing modes. This package does not include an automatic firmware-flashing or disk-wiping procedure.

## 8. Retain for another rebuild

Keep the source archive, complete Git revision history, this runbook, CPU baseline subdirectory, reference values, validation record, and version snapshot. Back up kubeconfig, registry authentication, repository private key, Vault recovery material and generated keys in a separate secret store. The source/archive alone is not an offline mirror: some base charts/operators float, and the CPU runbook explains that reproducibility boundary.

Upstream procedures: [Red Hat OSC GPU configuration](https://docs.redhat.com/en/documentation/openshift_sandboxed_containers/1.13/html/deploying_confidential_containers_on_bare-metal_servers/configure-cc-overview_metal-cc), [NVIDIA confidential-container release notes](https://docs.nvidia.com/datacenter/cloud-native/confidential-containers/latest/release-notes.html), [NVIDIA attestation](https://docs.nvidia.com/datacenter/cloud-native/confidential-containers/latest/attestation.html).
