# Contributing

Use Python 3.10 or newer on Linux, macOS, or WSL. The tests use the standard library and fake runners; they need no hypervisor:

```sh
python3 -m unittest discover -s tests -t . -q
```

Run one test process at a time. Tests must not start VMs, touch the network or depend on real hosts. Backend changes
need a test that checks the exact commands issued. Keep host names, addresses, paths and credentials out of code,
tests and docs; use the `SANDBOX_*` environment variables.
