# Changelog

## 1.2.0 - 2026-11-22

- Start the bundled Bluetooth receiver automatically when the backend plugin loads.
- Use one shared runtime directory for the backend and receiver sockets, PID file and log.
- Declare the D-Bus Python dependency in the backend package manifest.
- Register a BlueZ pairing agent and pass pairing prompts to the plugin interface.
- Add receiver health and host capability diagnostics.
- Add a GitHub Actions workflow for publishing both plugin packages.
- Publish the plugin under the original author namespace `bluetooth-audio@Nathanuk1231`.
