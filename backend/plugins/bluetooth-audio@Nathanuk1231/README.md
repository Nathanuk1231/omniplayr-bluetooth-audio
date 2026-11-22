# Bluetooth Audio

Bluetooth Audio adds a Bluetooth A2DP audio receiver to OmniPlayr. It lets phones, tablets and computers stream audio to the player.

The backend starts the bundled receiver when the plugin loads and exposes receiver status and pairing support to the OmniPlayr frontend.

## Server requirements

The OmniPlayr server must run on Linux with:

- A Bluetooth adapter managed by BlueZ.
- BlueZ and the system D-Bus service.
- PipeWire with Bluetooth audio support and an active audio session.
- `busctl`, `pw-dump`, `pw-record` and `ffmpeg`.

The backend process must be able to access the host Bluetooth D-Bus service and PipeWire audio socket. For Docker deployments, those host resources and the required user permissions must be passed into the backend container.

The plugin installs its declared Python dependency and starts its receiver process. It does not install host operating-system packages, enable system services, or change Docker permissions.

## Compatible devices

Any phone, tablet or computer with Bluetooth A2DP audio-source support can connect. The receiver running on the OmniPlayr server is currently Linux-only; native Windows and macOS receiver implementations are not included.

## Install

```sh
omniplayr install bluetooth-audio@Nathanuk1231
```

The OmniPlayr CLI installs the frontend and backend packages and registers the backend plugin.
