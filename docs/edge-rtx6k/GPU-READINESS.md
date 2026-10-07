# edge-rtx6k GPU readiness assessment

> **DRAFT ENGINEERING RUNBOOK / LAB RECORD — NOT PRODUCTION READY.**
> Historical pre-install assessment from 7 October 2026, not current cluster state or a vendor support certification. GPU deployment and model-key gating were subsequently validated. See the [current documentation index](README.md).

Historical pre-install assessment, 7 October 2026. See [the GPU extension runbook](REBUILD-RUNBOOK.md) for subsequent deployment results. Cluster inspection was read-only: no operators installed, node labels changed, drivers loaded, firmware changed, GPU modes changed, or reboots requested. No Slack messages were sent.

## Decision

The host passes the inspected CPU and PCIe prerequisites for a GPU passthrough experiment. It is **not yet verified for confidential GPU workloads**. GPU firmware and current confidential-computing mode remain unknown, and GPU resource management and GPU attestation are not configured.

The next engineering step is to prepare a corrected AMD GPU configuration and obtain a per-card VBIOS/CC-mode inventory. The first workload should request **one GPU in one confidential VM**. NVIDIA lists RTX PRO 6000 BSE for single-GPU confidential passthrough; that does not establish support for combining this server's four cards into one confidential VM. [NVIDIA supported configuration](https://docs.nvidia.com/datacenter/cloud-native/confidential-containers/latest/release-notes.html)

## Observed hardware

| Item | Observed result |
| --- | --- |
| Server | Dell PowerEdge XE7745 |
| BIOS | 1.7.6; DMI date 05/04/2026 |
| Node | `9c-63-c0-dd-06-52` |
| CPUs | AMD EPYC 9555, Turin; two sockets, as recorded in the baseline |
| CPU TEE | AMD SEV-SNP enabled; kernel reports SEV-SNP API 1.58, build 6 |
| IOMMU | AMD IOMMU active; interrupt remapping enabled |
| PCIe isolation | Each GPU has a separate IOMMU group containing only that GPU function |
| ACS | Request/completion redirection and upstream forwarding enabled on the inspected capable bridges in each GPU path |
| GPU driver binding | All four GPUs currently unbound; no NVIDIA, nouveau, or VFIO modules loaded |
| Host NVIDIA utility | `nvidia-smi` absent |
| GPU VBIOS / CC mode | Not established by this inspection |

| PCI address | Device | IOMMU group | NUMA node | Link |
| --- | --- | --- | --- | --- |
| `0000:42:00.0` | RTX PRO 6000 Blackwell Server Edition, `10de:2bb5` | 81 | 0 | 32 GT/s ×16 |
| `0000:8c:00.0` | Same | 45 | 0 | 32 GT/s ×16 |
| `0001:04:00.0` | Same | 145 | 1 | 32 GT/s ×16 |
| `0001:c8:00.0` | Same | 109 | 1 | 32 GT/s ×16 |

The inspected first GPU has a 128 GiB PCI BAR aperture. This is a PCI address-space allocation, **not a measurement of available GPU VRAM**. No firmware update requirement can be concluded from the inventory so far.

## What is already installed

| Component | State |
| --- | --- |
| OpenShift | 4.22.15 CPU baseline |
| Sandboxed Containers / Trustee | 1.13.0 / 1.2.0 |
| Kata RPM | `kata-containers-3.31.0-5.rhaos4.22.el9.x86_64` |
| QEMU package owning `/usr/libexec/qemu-kvm` | `qemu-kvm-core-10.1.0-17.el9_8.5.x86_64` |
| OVMF | `edk2-ovmf-20241117-8.el9.noarch` |
| GPU RuntimeClass | `kata-cc-nvidia-gpu`, handler `kata-snp-nvidia-gpu` |
| CRI-O handler | Points to `/etc/kata-containers/kata-snp-nvidia-gpu/configuration.toml` |
| GPU guest image | `kata-cc-nvidia-gpu-595.58.03.initrd`, under the recorded guest-kernel directory |
| Pod-resources socket | `/var/lib/kubelet/pod-resources/kubelet.sock` exists |
| GPU Operator / ClusterPolicy | Not installed; ClusterPolicy API absent |
| GPU resources | No NVIDIA resources in node Allocatable |
| NodeFeatureRule objects | Correction: `openshift-nfd/consolidated-hardware-features` already existed; the original query omitted `-A`. NVIDIA-specific rules were not present. |

The GPU runtime uses SNP OVMF, `confidential_guest=true`, cold-plug through root ports, eight PCIe root ports, and `vfio_mode="guest-kernel"`. Its defaults are one vCPU and 8 GiB guest memory. The GPU-specific kernel parameters include `pci=realloc pci=nocrs pci=assign-busses nvrc.smi.srs=1`.

Installed GPU initrd SHA-256:

```text
abd755287282a268578687137bf437caeda6139c1a654f18d13f8a72a68892ca
```

This hash records the node's current file. It has **not yet been independently checked against the release image** for a GPU measurement baseline. The CPU measurements in the rebuild bundle cannot substitute for GPU guest measurements.

The effective kubelet configuration did not explicitly list `KubeletPodResourcesGet`. Absence from the override map is not proof that a default feature is disabled. The socket exists; actual device-plugin allocation still needs a functional test. No feature-gate change was made.

## Compatibility and version choices

NVIDIA's documentation identifies this GPU model for single-GPU confidential passthrough. Its published reference architecture also documents restrictions on mixing confidential and nonconfidential GPUs on one host. Consequently, “test one GPU” means allocate one GPU to the first test VM; it does not promise that configuring CC mode will affect only one card. [NVIDIA release notes](https://docs.nvidia.com/datacenter/cloud-native/confidential-containers/latest/release-notes.html)

Red Hat's OSC 1.13 feature matrix names H100 and DGX B200 for bare-metal confidential GPU workloads. RTX PRO 6000 is not named there. This is a gap in evidence for the exact Dell/Turin/RTX/OpenShift combination, not proof that it cannot work. Treat this as an engineering validation until the product teams confirm coverage. [Red Hat feature matrix](https://docs.redhat.com/en/documentation/openshift_sandboxed_containers/1.13/html/deploying_confidential_containers_on_bare-metal_servers/cc-discover_metal-cc)

The Red Hat deployment procedure specifies GPU Operator 26.3.0 and a passthrough configuration with the GPU driver inside the Kata guest. It enables CC Manager, VFIO Manager, CDI, and the Kata sandbox device plugin, while disabling the ordinary host driver and NVIDIA Kata Manager. Keep OSC responsible for this cluster's Kata runtime. [Red Hat GPU configuration](https://docs.redhat.com/en/documentation/openshift_sandboxed_containers/1.13/html/deploying_confidential_containers_on_bare-metal_servers/configure-cc-overview_metal-cc)

The live `certified-operators` catalog currently advertises:

| Channel | Current CSV |
| --- | --- |
| `v26.3` | `gpu-operator-certified.v26.3.3` |
| `v26.7` and `stable` | `gpu-operator-certified.v26.7.1` |

Do not select the current default channel merely because it is available. Before installation, confirm the intended 26.3 release with the OSC/NVIDIA stack, verify the requested CSV remains available, use Manual approval, and review the actual InstallPlan. No exact GPU Operator version was installed or newly validated in this assessment.

## Repository defects to correct before enabling GPUs

These findings refer to deployment commit `5f158c30f120f9eb041d5d77a81fedb916684bb3`. A local Helm render simulated replacing the AMD CPU profile with the existing AMD GPU profile; nothing was applied.

1. **The GPU subscription and applications are commented out.** `values-baremetal.yaml` contains commented definitions for `gpu-operator`, `nvidia-gpu`, and `gpu-workload`. A profile switch produces none of these resources.
2. **The AMD GPU profile omits the Intel DCAP subscription disable.** The simulated render reintroduced `intel-tdx-dcap-operator`. Keep that subscription disabled on this server.
3. **The AMD GPU profile omits `tdx.enabled=false` for the baremetal chart.** That chart defaults to true and would generate Intel TDX MachineConfigs. Carry the CPU profile's correction forward.
4. **Trustee GPU attestation remains disabled.** The simulated Trustee Application still sets `kbs.gpu.enabled=false`. Enabling hardware access is insufficient; GPU evidence and the release policy need explicit configuration and tests.
5. **Keep SNP/VCEK and bare-metal references intact.** The extra VCEK values file currently still sets SNP enabled, and the topology keeps bare-metal references enabled. Preserve those settings while editing the GPU overrides. Helm replaces override arrays; inspect the complete final list.
6. **The generic GPU chart creates IOMMU MachineConfigs for both roles.** This host already has working IOMMU and isolated GPUs. Avoid triggering a SNO reboot solely by applying redundant kernel arguments; determine whether any additional host setting is actually needed.
7. **The GPU demo is not yet an acceptance test.** Its script runs vectorAdd followed by `sleep` without fail-fast behavior. A failed vectorAdd could leave a Running pod. Change the test so a nonzero CUDA result fails the workload, and verify GPU attestation separately.

The current global CPU RuntimeClass should remain the default for existing CPU demos. Configure `kata-cc-nvidia-gpu` explicitly for the GPU test workload.

## Concrete next sequence

### 1. Close firmware and stack questions

- Obtain each card's VBIOS version and current/pending CC mode through available vendor-supported inspection tooling or the server management inventory. No firmware or mode change is implied by collecting this information.
- Confirm RTX PRO 6000 BSE support in the chosen GPU Operator/CC Manager release and the downstream OSC guest driver 595.58.03 combination.
- Confirm the relevant firmware baseline for that exact stack. The older NVIDIA R580 notes list `98.02.81.00.01` for this GPU's single-GPU mode, but that is historical release evidence, not an instruction to flash it onto this server. [NVIDIA R580 release notes](https://docs.nvidia.com/580trd1-trusted-computing-solutions-release-notes.pdf)

### 2. Prepare and review the GitOps change

Enable the selected GPU Operator subscription and ClusterPolicy, fix the AMD profile defects above, and keep the first GPU workload gated until resources and reference values are ready. Render all affected charts. Preserve the stable LVMS disk selector, CPU attestation enforcement, VCEK mapping, and manual OSC/Trustee approvals. Commit and push only when proceeding with the deployment; Argo automatically reconciles the watched branch.

### 3. Configure host GPU resource management

Deploy the chosen operator and passthrough operands. Verify CC Manager readiness, VFIO binding, correct NVIDIA labels, and allocatable `nvidia.com/pgpu` resources. Account for node-wide GPU mode changes and any required reset/reboot on this single-node cluster. The absence of a host NVIDIA driver is intentional in this architecture; do not install a conventional host compute-driver stack as a prerequisite by assumption.

### 4. Establish GPU-aware attestation

Independently verify the GPU guest artifacts from the OCP release, calculate SNP measurements using the exact GPU guest image and command line, and retain the CPU references alongside the GPU references. Configure Trustee GPU verification and the corresponding initdata/evidence flow. Verify that the required NVIDIA attestation endpoints are reachable. Test that a policy requiring GPU evidence rejects missing or invalid GPU evidence.

### 5. Run one confidential GPU test

Request one `nvidia.com/pgpu` with `runtimeClassName: kata-cc-nvidia-gpu`. Use a failure-sensitive CUDA test, inspect reported GPU identity and CC state, verify CPU and GPU attestation, and require successful secret delivery only after both pass. Recheck that the CPU demos still work. A Running pod or successful vectorAdd alone is insufficient proof of confidential GPU execution.

### 6. Add inference

Choose a model and model server that fit a single card, then address model storage and attested key release. Extend the rebuild runbook with exact tested versions and GPU configuration after acceptance passes. Yuval's channel notes point to the separate confidential-inference pattern and divide the baseline and inference work; this inspection did not message or reassign anyone. [Pattern announcement](https://redhat-internal.slack.com/archives/C0C73J10WP4/p1791299602033929), [coordination message](https://redhat-internal.slack.com/archives/C0C73J10WP4/p1791300269431769)

## Completion criteria for the next deployment phase

- GPU firmware and mode recorded for each card.
- Exact operator and operand versions retained.
- GPU allocation visible and working through the existing OSC runtime.
- Single-GPU CUDA test succeeds inside an SNP confidential VM.
- GPU evidence is appraised and enforced by the release policy.
- Missing/invalid evidence fails secret release.
- Existing CPU baseline still passes.
- All successful changes and generated references are incorporated into reproducible documentation.

This assessment does not certify firmware, claim NVIDIA attestation has passed, or establish confidential inference performance. Those require the subsequent deployment and workload tests.
