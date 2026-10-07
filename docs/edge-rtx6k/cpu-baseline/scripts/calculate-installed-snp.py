import hashlib
import json
from pathlib import Path
from sevsnpmeasure.guest import snp_calc_launch_digest
from sevsnpmeasure.vcpu_types import CPU_SIGS

root = Path('/rootfs')
files = {
    'ovmf_file': ('usr/share/edk2/ovmf/OVMF.amdsev.fd', '1aa196fe94e56809aa668fc4eabfc50923131288e018bcf0c6ab4dd3581bd850'),
    'kernel': ('usr/share/kata-containers/osbuilder-images/6.12.0-211.16.1.el10_2.x86_64/vmlinuz', '0317da8a23853124ed4e4da6c56254f40a741ff4507e1afbbcb7f32e5c097206'),
    'initrd': ('usr/share/kata-containers/osbuilder-images/6.12.0-211.16.1.el10_2.x86_64/kata-cc.initrd', '1f9b3a90c60ec7a94985ae966fc66ecb4d04055ca3471b9f0074068f4dac8b62'),
}
paths = {}
for key, (relative, expected) in files.items():
    path = root / relative
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    assert actual == expected, f'{key} differs from verified release artifact'
    paths[key] = str(path)
template = (
    'tsc=reliable no_timer_check rcupdate.rcu_expedited=1 '
    'i8042.direct=1 i8042.dumbkbd=1 i8042.nopnp=1 i8042.noaux=1 '
    'noreplace-smp reboot=k cryptomgr.notests net.ifnames=0 '
    'pci=lastbus=0 console=hvc0 console=hvc1 debug panic=1 '
    'nr_cpus={n} selinux=0 scsi_mod.scan=none agent.log=debug '
    'agent.launch_process_timeout=6 cgroup_no_v1=all systemd.unified_cgroup_hierarchy=1'
)
measurements = [snp_calc_launch_digest(
    vcpus=n, vcpu_sig=CPU_SIGS['EPYC-v4'], guest_features=0x1,
    append=template.format(n=n), ovmf_hash_str='', **paths
).hex() for n in range(1, 33)]
print(json.dumps(dict(snp_launch_measurement=measurements, snp_bootloader=[3],
    snp_tee_svn=[2], snp_snp_svn=[6], snp_microcode=[117]), indent=2))
