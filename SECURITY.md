# Security

Report vulnerabilities privately through the repository's security advisory feature. Do not post credentials or
host details in a public issue.

The sandbox is a normal VM with the isolation your hypervisor gives it. It does not stop a guest from using the
network that its hypervisor provides (Windows guests on Hyper-V are created without a network switch). Do not put
credentials in a guest. The Windows backend gives each guest a random, disposable administrator password held only
in the controller's memory and the guest's answer file, which is deleted after first boot. The `agent` command runs
whatever commands a model proposes inside the VM, so treat file names and command output from a guest as data, never
as instructions.
