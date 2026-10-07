# GPU BAR recovery observed during Qwen bring-up

On 2026-10-07, the new confidential inference pod could not initialize either idle
GPU at `0000:42:00.0` or `0001:c8:00.0`. The existing encrypted-array test remained
healthy on `0000:8c:00.0`. NVIDIA CC manager was already in CrashLoopBackOff when
the failure was investigated.

Guest console excerpts:

```text
NVRM: The NVIDIA GPU 0000:02:00.0
NVRM: (PCI ID: 10de:2bb5) installed in this system has
NVRM: fallen off the bus and is not responding to commands.
nvidia 0000:02:00.0: probe with driver nvidia failed with error -1
NVRM: None of the NVIDIA devices were initialized.
Failed to start nvidia-cdi.service - Generate NVIDIA CDI Configuration.
unresolvable CDI devices nvidia.com/gpu=0
```

Kubernetes reported `failed to inject devices after CDI timeout of 100 seconds`.
The failure happened before the inference container or key-fetch code ran.

CC manager reported:

```text
BAR0 still broken after 3s sleep
Blackwell+ BAR0 not accessible and no DVSEC 10de:0 cap exposed
AttributeError: 'BrokenGpu' object has no attribute 'is_cc_query_supported'
```

PCI configuration showed the decisive discrepancy: the affected cards' BAR
address bits were zero (`BAR0 = 0x000000000000000c`), while Linux still reserved
valid address ranges. An MMIO read at the reserved BAR0 returned `ffffffff`.
The two already assigned GPUs had matching, nonzero BAR addresses. Idle D3 power
state was observed, but its role as the original trigger was not conclusively
established. Do not treat this as a proven firmware defect or a general fix for
every GPU initialization failure.

## Recovery performed

This was an administrative hardware recovery on unused cards, not an attestation
policy change. No CC mode, guest image, measured boot reference or firmware version
was changed.

1. Set the inference replica count to zero and waited for its failed sandboxes to
   exit. Verified QEMU command lines did not assign either target PCI function.
   The two existing GPU test VMs were left running.
2. A function-level reset and a reset of the isolated bridge containing only
   `0000:42:00.0` did not restore the missing addresses. Those attempts are not a
   required step of the successful recovery.
3. For each unused affected card, forced D0, unbound `vfio-pci`, disabled memory
   decoding, and restored its three 64-bit memory BARs from Linux's existing
   `/sys/bus/pci/devices/<BDF>/resource` reservations. No new addresses were invented
   or allocated. Preserved the BAR type flags, then restored memory decoding.
4. Rebound the card to `vfio-pci` so the driver captured the corrected configuration,
   then set `power/control` to `on` again. Restoring registers alone without
   rebinding had not survived the subsequent VFIO open/reset.
5. Verified the BAR registers matched the kernel reservations and BAR0 read
   `a100201b` on both recovered cards instead of `ffffffff`.
6. Recreated the failed CC-manager pod. Its fresh log confirmed all four GPUs were
   already in CC-on mode, and it set CC-ready true without changing GPU mode:

   ```text
   Found CC-capable GPU: 0000:42:00.0 - Generic-GB202
   Found CC-capable GPU: 0000:8c:00.0 - Generic-GB202
   Found CC-capable GPU: 0001:04:00.0 - Generic-GB202
   Found CC-capable GPU: 0001:c8:00.0 - Generic-GB202
   All gpus already set to cc on, skipping
   Set nvidia.com/cc.mode.state=on, nvidia.com/cc.ready.state=true
   ```

7. Retried normal confidential startup. Host register recovery was **not a complete
   fix**: assigning either recovered card to a VM subsequently produced a fatal
   PCIe malformed-TLP error and stopped the VM:

   ```text
   vfio-pci 0000:42:00.0: AER: aer_status: 0x00040000
   vfio-pci 0000:42:00.0: [18] MalfTLP (First)
   qemu-kvm: vfio_err_notifier_handler(0000:42:00.0) Unrecoverable error detected.
   vfio-pci 0001:c8:00.0: AER: aer_status: 0x00040000
   vfio-pci 0001:c8:00.0: [18] MalfTLP (First)
   pcieport 0001:c6:01.0: AER: device recovery failed
   ```

8. Stopped the failed inference VMs, verified both cards were unassigned, and
   unbound only `0000:42:00.0` and `0001:c8:00.0` from `vfio-pci`. Retained their
   `vfio-pci` driver override so another host GPU driver would not claim them.
   Restarted the sandbox device plugin to rediscover the actual usable inventory.
   It now advertises **two** GPUs: `vfio1` (`0000:8c:00.0`) and `vfio2`
   (`0001:04:00.0`). The earlier vector-add deployment was scaled to zero to make
   its GPU available to Qwen; the encrypted-array test stayed running.

The two quarantined cards require further hardware/runtime investigation. A
healthy CC-manager pod and a CC-on flag do not prove successful GPU initialization
inside a confidential VM. The model-key policy continues requiring CPU and GPU
attestation, so an unavailable GPU does not result in CPU-only key release.

The recovery and quarantine were performed without rebooting the node. The
per-device power-control and unbind operations are runtime state, not persistent
MachineConfig changes. A host reboot or VFIO-manager restart can rebind the cards;
recheck inventory and quarantine them again before scheduling until the fault is
resolved. The general root cause remains unverified. If the issue
recurs, capture the BAR/configuration discrepancy and engage the GPU/runtime
maintainers before applying broad power-management or firmware changes.

Do not rewrite BARs, unbind a device or reset a bridge while any VM or other process
owns that device. This record describes the diagnosed lab incident; it is not an
automatic installation step or a substitute for checking device ownership.
