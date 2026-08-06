# Momi Forge startup before Windows sign-in

Momi Forge can run automatically after a restart and remain available when all
users are signed out. The startup setup uses Windows Scheduled Tasks running as
the built-in `SYSTEM` service account, so it does not store or require a user's
Windows password.

## Install

1. Double-click `install_momi_forge_startup.bat`.
2. Accept the one-time Windows Administrator/UAC prompt.
3. Restart Windows.

On the next boot, Windows starts these background components before sign-in:

- Momi Forge app on port `8188`
- History Portal on port `8199`
- RunPod Manager backend on port `8843`

They do not open terminal windows. Each component is supervised and restarted
automatically if it exits unexpectedly.

## Verify

After Windows starts, another computer on the same network can open the normal
Momi Forge address even while the host PC is still showing the sign-in screen.
The host PC must be powered on, connected to the network, and not asleep.

The tasks appear in Windows Task Scheduler with names beginning with
`Momi Forge -`.

Runtime and supervisor logs are written to:

```text
D:\Momi Forge\logs\startup
```

The latest 30 process runs per component are retained.

## Remove

Double-click `uninstall_momi_forge_startup.bat` and accept the Administrator/UAC
prompt. This stops and removes only the three Momi Forge boot tasks; application
data and logs are not deleted.

## Notes

- The one-time Administrator prompt is required because pre-login tasks are
  machine-level Windows configuration.
- Signing out does not stop these tasks because they run as `SYSTEM`, not as the
  interactive user.
- The app is not reachable while Windows is shut down or the PC is asleep.
- Existing tasks named `Momi-AI` or `Momi-AI-Boot` are unrelated and are not
  modified by this installer.
