So it turns out I misunderstood the description of PyInstaller. It does not compile the Python bytecode to a native application, but rather just packages the Python runtime as an executable.

While this was fun, it's useless. It uses way more RAM (and likely more CPU percent) than the default Fancontrol. Use that instead.
