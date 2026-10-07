"""Recompute edge-rtx6k CPU and confidential GPU SNP references from OCP 4.22.15."""
import hashlib
import json
import logging
from pathlib import Path

from veritas.platforms.baremetal import BaremetalExtractor, DEFAULT_KERNEL_CMDLINE

EXPECTED = {
    "ovmf_snp": "1aa196fe94e56809aa668fc4eabfc50923131288e018bcf0c6ab4dd3581bd850",
    "vmlinuz": "0317da8a23853124ed4e4da6c56254f40a741ff4507e1afbbcb7f32e5c097206",
    "initrd": "1f9b3a90c60ec7a94985ae966fc66ecb4d04055ca3471b9f0074068f4dac8b62",
}


class EdgeExtractor(BaremetalExtractor):
    def _kernel_cmdlines(self, is_gpu=False):
        # Kata 3.31 on OCP 4.22.15 includes this argument. Veritas 0.1.3rc1
        # omits it from its default. CPU count is otherwise generated normally.
        template = DEFAULT_KERNEL_CMDLINE.replace(
            "agent.log=debug ", "agent.log=debug agent.launch_process_timeout=6 "
        )
        if is_gpu:
            template = template.replace(
                "cgroup_no_v1=all systemd.unified_cgroup_hierarchy=1",
                "cgroup_no_v1=all pci=realloc pci=nocrs pci=assign-busses nvrc.smi.srs=1",
            )
        return [template.format(nr_cpus=n) for n in range(1, 33)]

    def _compute_snp_values(self, artifact_paths, is_gpu=False):
        if is_gpu and not Path(artifact_paths["initrd"]).name.startswith("kata-cc-nvidia-gpu-"):
            return []  # Exclude nonconfidential GPU guest images.
        expected_hashes = dict(EXPECTED)
        if is_gpu:
            expected_hashes["initrd"] = "abd755287282a268578687137bf437caeda6139c1a654f18d13f8a72a68892ca"
        for key, expected in expected_hashes.items():
            actual = hashlib.sha256(Path(artifact_paths[key]).read_bytes()).hexdigest()
            if actual != expected:
                raise RuntimeError(f"Release artifact does not match installed {key}: {actual}")
        return super()._compute_snp_values(artifact_paths, is_gpu=is_gpu)


logging.basicConfig(level=logging.INFO)
extractor = EdgeExtractor(
    "snp", authfile="/pattern-home/pull-secret.json", ocp_versions=["4.22.15"]
)
values = {v.name: v.values for v in extractor.extract()}
assert len(values["snp_launch_measurement"]) == 64
# Exact firmware versions independently obtained from this host's VCEK request.
values.update(snp_bootloader=[3], snp_tee_svn=[2], snp_snp_svn=[6], snp_microcode=[117])
output = Path("/pattern-home/.coco-pattern/firmware-reference-values.json")
output.write_text(json.dumps(values, indent=2) + "\n")
output.chmod(0o600)
print("Saved 32 CPU + 32 GPU launch measurements and exact TCB reference values.")
