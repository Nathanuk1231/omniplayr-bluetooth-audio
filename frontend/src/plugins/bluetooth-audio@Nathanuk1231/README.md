# Bluetooth Audio

Bluetooth Audio adds a tab to OmniPlayr for connecting and managing Bluetooth audio source devices.

Connect a phone, tablet or computer that supports Bluetooth A2DP audio streaming and play its audio through OmniPlayr. The frontend works with the OmniPlayr web interface; the receiver itself currently runs on a Linux server.

The backend requires BlueZ, system D-Bus and PipeWire, and must be able to access the host Bluetooth and audio sockets. See the repository README for the full server requirements and Docker notes.

Install both parts from the root of an OmniPlayr project with:

```sh
omniplayr install bluetooth-audio@Nathanuk1231
```
