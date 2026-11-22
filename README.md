# Bluetooth Audio for OmniPlayr

Bluetooth Audio lets you play audio from your phone, tablet or computer through OmniPlayr using a Bluetooth connection.

## Features

- Receive audio over Bluetooth using A2DP.
- Pair and manage Bluetooth devices through OmniPlayr.
- View receiver status and system capability checks.
- Automatically start the Bluetooth receiver when the backend loads.
- Install through the OmniPlayr Plugin Registry.

## Requirements

The OmniPlayr server must run on Linux and have the following components installed:

- A Bluetooth adapter supported by BlueZ.
- BlueZ and the system D-Bus service.
- PipeWire with Bluetooth audio support.
- `busctl`, `pw-dump`, `pw-record` and `ffmpeg`.

The backend must have access to the host's Bluetooth D-Bus service and PipeWire audio socket. For Docker deployments, these resources and the required permissions must be made available to the backend container.

The plugin handles its own Python dependency and receiver startup. System packages and host services must already be available.

## Compatible Devices

Any phone, tablet or computer that supports Bluetooth A2DP audio streaming can connect.

This includes Android, iOS, Windows and macOS devices.

The receiver currently runs on Linux. Windows and macOS server support is not currently available.

## Installation

Install Bluetooth Audio through the OmniPlayr CLI:

```bash
omniplayr install bluetooth-audio@Nathanuk1231
```

The CLI installs both the frontend and backend components.

## Troubleshooting

If Bluetooth Audio isn't working, check the following:

1. Make sure the Bluetooth adapter is detected by Linux.
2. Verify that BlueZ and D-Bus are running.
3. Check that PipeWire is running and has an active audio session.
4. Make sure the backend has access to the required Bluetooth and PipeWire sockets.
5. Verify that all required system dependencies are installed.

The Bluetooth Audio tab also provides system capability checks to help identify missing components.

## License

This project is licensed under the [MIT License](LICENSE).