#!/usr/bin/env python3
"""Optional OmniPlayr Bluetooth A2DP bridge with AVRCP controls and pairing helpers."""
import asyncio
import json
import os
import queue
import re
import shutil
import signal
import socket
import socketserver
import stat
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(os.environ.get("OMNIPLAYR_PROJECT_DIR", "/compose")).expanduser().resolve()
RUNTIME_DIR = Path(os.environ.get("OMNIPLAYR_RUNTIME_DIR", str(PROJECT_ROOT / ".runtime"))).expanduser().resolve()
SOCKET_DIR = RUNTIME_DIR / "bluetooth-audio"
SOCKET_PATH = SOCKET_DIR / "receiver.sock"
PAIRING_SOCKET_PATH = SOCKET_DIR / "pairing.sock"
PID_PATH = SOCKET_DIR / "receiver.pid"
SAMPLE_RATE = 44100
CHANNELS = 2
BITRATE = "192k"
CHUNK_SIZE = 4096
CLIENT_QUEUE_CHUNKS = 3  # Deliberately short to avoid replaying stale buffered audio.
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def run_text(args: list[str], timeout: float = 3.0) -> str:
    try:
        result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout,
                                env={**os.environ, "LC_ALL": "C"})
        return (result.stdout or "") + (result.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)


def bluetoothctl(*args: str, timeout: float = 5.0) -> str:
    return run_text([shutil.which("bluetoothctl") or "/usr/bin/bluetoothctl", *args], timeout)


def parse_devices(mode: str | None = None) -> list[dict[str, str]]:
    output = bluetoothctl(*(["devices"] + ([mode] if mode else [])))
    devices: list[dict[str, str]] = []
    for line in output.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) >= 3 and parts[0] == "Device" and MAC_RE.fullmatch(parts[1]):
            devices.append({"address": parts[1].upper(), "name": parts[2]})
    return devices


def connected_devices() -> list[dict[str, str]]:
    return parse_devices("Connected")


def bluez_player_paths(devices: list[dict[str, str]]) -> list[tuple[dict[str, str], str]]:
    busctl = shutil.which("busctl") or "/usr/bin/busctl"
    tree = run_text([busctl, "--system", "tree", "org.bluez"], timeout=3.0)
    found: list[tuple[dict[str, str], str]] = []
    for device in devices:
        device_id = device["address"].replace(":", "_")
        pattern = rf"(/org/bluez/hci\d+/dev_{re.escape(device_id)}/player\d+)"
        for path in re.findall(pattern, tree):
            found.append((device, path))
    return found


def bus_property(path: str, prop: str) -> Any:
    """Read a BlueZ property, unwrapping both scalar variants and a{sv} maps."""
    busctl = shutil.which("busctl") or "/usr/bin/busctl"
    raw = run_text([busctl, "--system", "--json=short", "get-property", "org.bluez", path,
                    "org.bluez.MediaPlayer1", prop], timeout=2.5)
    try:
        raw = raw[raw.find("{"):] if "{" in raw else raw
        payload = json.loads(raw)
        signature = payload.get("type")
        value = payload.get("data")
        if signature == "a{sv}" and isinstance(value, dict):
            # busctl represents each dictionary value as a typed variant:
            # {"Title":{"type":"s","data":"..."}, ...}
            return {
                key: (item.get("data") if isinstance(item, dict) and "data" in item else item)
                for key, item in value.items()
            }
        if isinstance(value, dict) and "data" in value:
            return value.get("data")
        return value
    except (ValueError, AttributeError, TypeError):
        return None



def album_art_lookup(title: str | None, artist: str | None, album: str | None = None) -> str | None:
    """Find matching cover art asynchronously through the iTunes catalogue."""
    if not title or not artist:
        return None

    if not hasattr(album_art_lookup, "cache"):
        album_art_lookup.cache = {}
        album_art_lookup.pending = set()
        album_art_lookup.lock = threading.Lock()

    def normalise(value: str) -> str:
        import unicodedata
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
        return "".join(c.lower() for c in value if c.isalnum())

    key = (normalise(title), normalise(artist), normalise(album or ""))
    now = time.time()

    with album_art_lookup.lock:
        cached = album_art_lookup.cache.get(key)
        if cached and now - cached[0] < 21600:
            return cached[1]
        if key in album_art_lookup.pending:
            return None
        album_art_lookup.pending.add(key)

    def worker():
        artwork = None
        try:
            import urllib.parse
            import urllib.request

            query = urllib.parse.urlencode({
                "term": f"{title} {artist}",
                "entity": "song",
                "limit": 10,
            })
            request = urllib.request.Request(
                "https://itunes.apple.com/search?" + query,
                headers={"User-Agent": "OmniPlayr-Bluetooth/1.0"}
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                results = json.loads(response.read().decode("utf-8")).get("results", [])

            wanted_title = normalise(title)
            wanted_artist = normalise(artist)

            matches = [
                item for item in results
                if normalise(item.get("trackName", "")) == wanted_title
                and normalise(item.get("artistName", "")) == wanted_artist
            ]

            if matches:
                if album:
                    matching_album = [
                        item for item in matches
                        if normalise(item.get("collectionName", "")) == normalise(album)
                    ]
                    if matching_album:
                        matches = matching_album

                artwork = matches[0].get("artworkUrl600") or matches[0].get("artworkUrl100")
                if artwork:
                    artwork = artwork.replace("100x100bb", "600x600bb")

        except Exception as error:
            print(f"Album artwork lookup failed: {error}")

        with album_art_lookup.lock:
            album_art_lookup.cache[key] = (time.time(), artwork)
            album_art_lookup.pending.discard(key)

    threading.Thread(target=worker, daemon=True).start()
    return None


def bluetooth_track_metadata(devices: list[dict[str, str]]) -> dict[str, Any] | None:
    for device, path in bluez_player_paths(devices):
        track = bus_property(path, "Track")
        if not isinstance(track, dict):
            continue
        try:
            duration_ms = int(track.get("Duration") or 0)
        except (TypeError, ValueError):
            duration_ms = 0
        try:
            position_ms = max(0, int(bus_property(path, "Position") or 0))
        except (TypeError, ValueError):
            position_ms = 0
        status = str(bus_property(path, "Status") or "unknown").lower()
        metadata = {
            "title": str(track.get("Title") or "").strip() or None,
            "artist": str(track.get("Artist") or "").strip() or None,
            "album": str(track.get("Album") or "").strip() or None,
            "duration_ms": duration_ms,
            "position_ms": position_ms,
            "status": status,
            "device_name": device["name"],
            "device_address": device["address"],
            "player_path": path,
        }
        metadata["album_art"] = album_art_lookup(
            metadata["title"], metadata["artist"], metadata["album"]
        )
        if metadata["title"] or metadata["artist"] or metadata["album"]:
            return metadata
    return None


def pipewire_audio_nodes() -> list[dict[str, str]]:
    executable = shutil.which("pw-dump") or "/usr/bin/pw-dump"
    output = run_text([executable], timeout=2.5)
    try:
        objects = json.loads(output[output.find("["):])
    except (ValueError, TypeError):
        return []
    nodes: list[dict[str, str]] = []
    for item in objects if isinstance(objects, list) else []:
        props = item.get("info", {}).get("props", {})
        name = str(props.get("node.name", ""))
        if name.startswith("bluez_input."):
            nodes.append({"name": name, "description": str(props.get("node.description") or props.get("node.nick") or "Bluetooth audio")})
    return nodes


class Receiver:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.clients: set[queue.Queue[bytes | None]] = set()
        self.capture_thread: threading.Thread | None = None
        self.capture_error: str | None = None
        self.stopping = threading.Event()
        self.pw_process: subprocess.Popen[bytes] | None = None
        self.ffmpeg_process: subprocess.Popen[bytes] | None = None
        self.pair_job: dict[str, Any] = {"state": "idle", "message": ""}
        self.adapter_mode: str | None = None
        self.pairing_lock = threading.RLock()
        self.pairing_event = threading.Event()
        self.pairing_request: dict[str, Any] | None = None
        self.pairing_result: dict[str, Any] | None = None
        self.pairing_agent_ready = False
        self.pairing_agent_error: str | None = None

    def status(self) -> dict[str, Any]:
        connected = connected_devices()
        paired = parse_devices("Paired")
        known = parse_devices()
        nodes = pipewire_audio_nodes()
        metadata = bluetooth_track_metadata(connected) if connected else None
        adapter = bluetoothctl("show")
        with self.lock:
            capture_running = bool(self.capture_thread and self.capture_thread.is_alive() and self.ffmpeg_process and self.ffmpeg_process.poll() is None)
            listeners = len(self.clients)
            capture_error = self.capture_error
            pair_job = dict(self.pair_job)
        missing_tools = [name for name in ("bluetoothctl", "busctl", "pw-dump", "pw-record", "ffmpeg") if not shutil.which(name)]
        dbus_socket = Path(os.environ.get("OMNIPLAYR_DBUS_SOCKET", "/run/dbus/system_bus_socket"))
        adapter_available = bool(re.search(r"Controller\s+[0-9A-Fa-f:]{17}", bluetoothctl("list")))
        pipewire_output = run_text([shutil.which("pw-dump") or "/usr/bin/pw-dump"], timeout=2.5)
        try:
            pipewire_objects = json.loads(pipewire_output[pipewire_output.find("["):])
            pipewire_available = isinstance(pipewire_objects, list)
        except (ValueError, TypeError):
            pipewire_available = False
        host_error = None
        if missing_tools:
            host_error = "Missing host tools: " + ", ".join(missing_tools)
        elif not dbus_socket.exists():
            host_error = f"The system D-Bus socket is unavailable at {dbus_socket}."
        elif not adapter_available:
            host_error = "No Bluetooth controller is visible to the receiver. Check the host adapter and BlueZ service."
        elif not pipewire_available:
            host_error = "The PipeWire audio session is unavailable to the receiver."
        return {
            "online": True,
            "host_platform": "Linux",
            "host_supported": True,
            "host_ready": host_error is None,
            "missing_tools": missing_tools,
            "dbus_available": dbus_socket.exists(),
            "bluetooth_adapter_available": adapter_available,
            "pipewire_available": pipewire_available,
            "host_error": host_error,
            "bluetooth_connected": bool(connected),
            "device_name": connected[0]["name"] if connected else None,
            "connected_devices": connected,
            "paired_devices": paired,
            "devices": known,
            "audio_streaming": bool(nodes),
            "audio_nodes": nodes,
            "metadata": metadata,
            "listeners": listeners,
            "capture_running": capture_running,
            "capture_error": capture_error,
            "discoverable": bool(re.search(r"Discoverable:\s+yes", adapter)),
            "pairable": bool(re.search(r"Pairable:\s+yes", adapter)),
            "pair_job": pair_job,
            "pairing_agent_ready": self.pairing_agent_ready,
        }

    def pairing_status(self) -> dict[str, Any]:
        with self.pairing_lock:
            request = dict(self.pairing_request) if self.pairing_request else None
        return {"online": self.pairing_agent_ready, "pairing_request": request, "error": self.pairing_agent_error}

    def pairing_decision(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = str(payload.get("id") or "")
        decision = str(payload.get("decision") or "").lower()
        with self.pairing_lock:
            request = self.pairing_request
            if not request or request.get("id") != request_id:
                return {"ok": False, "error": "Pairing request expired."}
            if request.get("display_only"):
                return {"ok": False, "error": "This pairing code is informational only."}
            if decision not in {"accept", "reject", "submit"}:
                return {"ok": False, "error": "Unknown pairing decision."}
            if decision == "submit":
                value = str(payload.get("value") or "").strip()
                if request.get("type") == "enter_pin" and not value:
                    return {"ok": False, "error": "Enter the PIN shown on your device."}
                if request.get("type") == "enter_passkey" and (not value.isdigit() or len(value) != 6):
                    return {"ok": False, "error": "Enter the six-digit passkey."}
                self.pairing_result = {"accepted": True, "value": value}
            else:
                self.pairing_result = {"accepted": decision == "accept", "value": None}
            self.pairing_event.set()
        return {"ok": True, "message": "Pairing response sent."}

    def clear_pairing_request(self, request_id: str | None = None) -> None:
        with self.pairing_lock:
            if request_id and self.pairing_request and self.pairing_request.get("id") != request_id:
                return
            self.pairing_request = None
            self.pairing_result = None

    def show_pairing_request(self, kind: str, device: str, message: str, passkey: str | None = None, entered: int | None = None) -> None:
        address = device.rsplit("/dev_", 1)[-1].replace("_", ":") if "/dev_" in device else ""
        known = next((item for item in parse_devices() if item["address"].lower() == address.lower()), None)
        request = {
            "id": uuid.uuid4().hex,
            "type": kind,
            "device_name": known["name"] if known else address or "Bluetooth device",
            "address": address,
            "passkey": passkey,
            "entered": entered,
            "display_only": True,
            "message": message,
            "expires_at": time.time() + 30,
        }
        with self.pairing_lock:
            self.pairing_request = request
            self.pairing_result = None
        threading.Thread(target=self._expire_pairing_request, args=(request["id"], 30), daemon=True).start()

    def _expire_pairing_request(self, request_id: str, delay: int) -> None:
        if not self.stopping.wait(delay):
            self.clear_pairing_request(request_id)

    def request_pairing_input(self, kind: str, device: str, message: str, passkey: str | None = None) -> str | None:
        address = device.rsplit("/dev_", 1)[-1].replace("_", ":") if "/dev_" in device else ""
        known = next((item for item in parse_devices() if item["address"].lower() == address.lower()), None)
        request = {
            "id": uuid.uuid4().hex,
            "type": kind,
            "device_name": known["name"] if known else address or "Bluetooth device",
            "address": address,
            "passkey": passkey,
            "entered": None,
            "display_only": False,
            "message": message,
            "expires_at": time.time() + 90,
        }
        with self.pairing_lock:
            self.pairing_request = request
            self.pairing_result = None
            self.pairing_event.clear()
        threading.Thread(target=self._expire_pairing_request, args=(request["id"], 90), daemon=True).start()
        if not self.pairing_event.wait(90):
            self.clear_pairing_request(request["id"])
            raise RuntimeError("Bluetooth pairing confirmation timed out.")
        with self.pairing_lock:
            result = dict(self.pairing_result or {})
            self.pairing_request = None
            self.pairing_result = None
        if not result.get("accepted"):
            raise PermissionError("Bluetooth pairing was rejected.")
        return result.get("value")

    def manage_discoverability(self) -> None:
        while not self.stopping.wait(3):
            desired = "connected" if connected_devices() else "idle"
            with self.lock:
                changed = self.adapter_mode != desired
                if changed:
                    self.adapter_mode = desired
            if changed:
                bluetoothctl("pairable-timeout", "0", timeout=2)
                bluetoothctl("discoverable-timeout", "0", timeout=2)
                if desired == "connected":
                    bluetoothctl("discoverable", "off", timeout=2)
                    bluetoothctl("pairable", "off", timeout=2)
                else:
                    bluetoothctl("pairable", "on", timeout=2)
                    bluetoothctl("discoverable", "on", timeout=2)

    def start_scan(self) -> dict[str, Any]:
        if connected_devices():
            return {"ok": False, "error": "Disconnect the currently connected device before pairing another."}
        for args in (("pairable-timeout", "0"), ("discoverable-timeout", "0"), ("pairable", "on"), ("discoverable", "on")):
            bluetoothctl(*args)
        output = bluetoothctl("scan", "on", timeout=4)
        threading.Thread(target=self._stop_scan_later, daemon=True).start()
        return {"ok": True, "message": "Scanning for Bluetooth devices.", "details": output[-400:]}

    def _stop_scan_later(self) -> None:
        if not self.stopping.wait(25):
            bluetoothctl("scan", "off", timeout=3)

    def pair(self, address: str) -> dict[str, Any]:
        address = address.upper()
        if not MAC_RE.fullmatch(address):
            return {"ok": False, "error": "Invalid Bluetooth device address."}
        if connected_devices():
            return {"ok": False, "error": "Disconnect the currently connected device before pairing another."}
        with self.lock:
            if self.pair_job.get("state") == "running":
                return {"ok": False, "error": "A pairing attempt is already running."}
            self.pair_job = {"state": "running", "address": address, "message": "Pairing request sent."}
        bluetoothctl("pairable", "on")
        bluetoothctl("discoverable", "on")
        threading.Thread(target=self._pair_worker, args=(address,), daemon=True).start()
        return {"ok": True, "message": "Pairing started. Follow the code prompt in the Bluetooth Audio tab."}

    def _pair_worker(self, address: str) -> None:
        output = bluetoothctl("pair", address, timeout=100)
        lowered = output.lower()
        ok = "pairing successful" in lowered or "pair successful" in lowered or "already paired" in lowered or "paired: yes" in lowered
        with self.lock:
            self.pair_job = {"state": "success" if ok else "failed", "address": address,
                             "message": output.strip()[-700:] or ("Pairing complete." if ok else "Pairing failed or timed out.")}
        bluetoothctl("scan", "off", timeout=3)

    def device_action(self, action: str, address: str | None = None) -> dict[str, Any]:
        connected = connected_devices()
        target = (address or (connected[0]["address"] if connected else "")).upper()
        if action == "connect":
            if not target or not MAC_RE.fullmatch(target):
                return {"ok": False, "error": "Select a known Bluetooth device first."}
            if connected and all(item["address"] != target for item in connected):
                return {"ok": False, "error": "Another Bluetooth device is already connected. Disconnect it first."}
            output = bluetoothctl("connect", target, timeout=15)
            return {"ok": "successful" in output.lower() or "connected" in output.lower(), "message": output.strip()[-500:] or "Connect requested."}
        if action == "disconnect":
            if not target:
                return {"ok": True, "message": "No Bluetooth device is connected."}
            output = bluetoothctl("disconnect", target, timeout=8)
            lowered = output.lower()
            return {"ok": "disconnect" in lowered or "not connected" in lowered or not output.strip(), "message": output.strip()[-500:] or "Disconnect requested."}
        if action == "forget":
            if not target or not MAC_RE.fullmatch(target):
                return {"ok": False, "error": "Select a valid paired device first."}
            bluetoothctl("disconnect", target, timeout=5)
            output = bluetoothctl("remove", target, timeout=8)
            lowered = output.lower()
            return {"ok": "removed" in lowered or "not available" in lowered or not output.strip(), "message": output.strip()[-500:] or "Device removed from paired devices."}
        return {"ok": False, "error": "Unsupported Bluetooth device action."}

    def media_action(self, action: str) -> dict[str, Any]:
        players = bluez_player_paths(connected_devices())
        if not players:
            return {"ok": False, "error": "No connected Bluetooth media player is available."}
        method_name = {"play": "Play", "pause": "Pause", "next": "Next", "previous": "Previous"}.get(action)
        if not method_name:
            return {"ok": False, "error": "Unsupported media action."}
        busctl = shutil.which("busctl") or "/usr/bin/busctl"
        _device, path = players[0]
        output = run_text([busctl, "--system", "call", "org.bluez", path, "org.bluez.MediaPlayer1", method_name], timeout=4)
        if "Error" in output or "failed" in output.lower():
            return {"ok": False, "error": output[-500:]}
        return {"ok": True, "message": f"{method_name} sent to the Bluetooth device."}

    def add_client(self) -> queue.Queue[bytes | None]:
        client_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=CLIENT_QUEUE_CHUNKS)
        with self.lock:
            self.clients.add(client_queue)
            self.capture_error = None
            if not self.capture_thread or not self.capture_thread.is_alive():
                self.capture_thread = threading.Thread(target=self._capture_loop, name="bluetooth-audio-capture", daemon=True)
                self.capture_thread.start()
        return client_queue

    def remove_client(self, client_queue: queue.Queue[bytes | None]) -> None:
        with self.lock:
            self.clients.discard(client_queue)

    def _publish(self, data: bytes) -> None:
        with self.lock:
            clients = list(self.clients)
        for client_queue in clients:
            try:
                client_queue.put_nowait(data)
            except queue.Full:
                try:
                    client_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    client_queue.put_nowait(data)
                except queue.Full:
                    pass

    def _capture_loop(self) -> None:
        pw_executable = shutil.which("pw-record") or "/usr/bin/pw-record"
        ffmpeg_executable = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
        pw_command = [pw_executable, "--target", "auto_null", "-P", "stream.capture.sink=true", "--rate", str(SAMPLE_RATE), "--channels", str(CHANNELS), "--format", "s16", "-"]
        ffmpeg_command = [ffmpeg_executable, "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-i", "pipe:0", "-vn", "-c:a", "libmp3lame", "-b:a", BITRATE, "-flush_packets", "1", "-f", "mp3", "pipe:1"]
        while not self.stopping.is_set():
            pw = ffmpeg = None
            try:
                pw = subprocess.Popen(pw_command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
                assert pw.stdout is not None
                ffmpeg = subprocess.Popen(ffmpeg_command, stdin=pw.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
                pw.stdout.close()
                with self.lock:
                    self.pw_process, self.ffmpeg_process, self.capture_error = pw, ffmpeg, None
                assert ffmpeg.stdout is not None
                while not self.stopping.is_set():
                    data = ffmpeg.stdout.read(CHUNK_SIZE)
                    if not data:
                        raise RuntimeError("MP3 encoder stopped producing audio")
                    self._publish(data)
            except Exception as exc:
                with self.lock:
                    self.capture_error = str(exc)
            finally:
                for process in (ffmpeg, pw):
                    if process and process.poll() is None:
                        try:
                            process.terminate()
                            process.wait(timeout=2)
                        except (subprocess.TimeoutExpired, OSError):
                            try:
                                process.kill()
                            except OSError:
                                pass
                with self.lock:
                    self.pw_process = self.ffmpeg_process = None
                if not self.stopping.is_set():
                    time.sleep(2)

    def stream_to(self, connection: socket.socket) -> None:
        client_queue = self.add_client()
        try:
            while not self.stopping.is_set():
                try:
                    data = client_queue.get(timeout=10)
                except queue.Empty:
                    continue
                if data is None:
                    break
                connection.sendall(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.remove_client(client_queue)

    def shutdown(self) -> None:
        self.stopping.set()
        self.pairing_event.set()
        with self.lock:
            clients = list(self.clients)
            processes = (self.ffmpeg_process, self.pw_process)
        for item in clients:
            try:
                item.put_nowait(None)
            except queue.Full:
                pass
        for process in processes:
            if process and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass


async def run_pairing_agent() -> None:
    from dbus_next import DBusError
    from dbus_next.aio import MessageBus
    from dbus_next.constants import BusType
    from dbus_next.service import ServiceInterface, method

    agent_path = "/org/omniplayr/bluetooth_audio/agent"

    class BluetoothAgent(ServiceInterface):
        def __init__(self, target: Receiver) -> None:
            super().__init__("org.bluez.Agent1")
            self.target = target

        async def ask(self, kind: str, device: str, message: str, passkey: str | None = None) -> str | None:
            try:
                return await asyncio.to_thread(self.target.request_pairing_input, kind, device, message, passkey)
            except (RuntimeError, PermissionError) as exc:
                raise DBusError("org.bluez.Error.Rejected", str(exc)) from exc

        @method()
        async def Release(self):
            self.target.pairing_agent_ready = False

        @method()
        async def RequestPinCode(self, device: "o") -> "s":
            value = await self.ask("enter_pin", device, "Enter the PIN displayed by the Bluetooth device.")
            return str(value or "")

        @method()
        async def DisplayPinCode(self, device: "o", pincode: "s"):
            self.target.show_pairing_request("display_pin", device, "Enter this PIN on your Bluetooth device.", pincode)

        @method()
        async def RequestPasskey(self, device: "o") -> "u":
            value = await self.ask("enter_passkey", device, "Enter the six-digit passkey displayed by the Bluetooth device.")
            return int(value or 0)

        @method()
        async def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
            self.target.show_pairing_request("display_passkey", device, "Enter this passkey on your Bluetooth device.", f"{passkey:06d}", int(entered))

        @method()
        async def RequestConfirmation(self, device: "o", passkey: "u"):
            await self.ask("confirm", device, "Confirm that the same passkey is shown on both devices.", f"{passkey:06d}")

        @method()
        async def RequestAuthorization(self, device: "o"):
            await self.ask("authorize", device, "Allow this Bluetooth device to pair?")

        @method()
        async def AuthorizeService(self, device: "o", uuid_value: "s"):
            await self.ask("authorize_service", device, f"Allow the Bluetooth service {uuid_value}?", uuid_value)

        @method()
        async def Cancel(self):
            self.target.clear_pairing_request()
            self.target.pairing_event.set()

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    bus.export(agent_path, BluetoothAgent(receiver))
    introspection = await bus.introspect("org.bluez", "/")
    proxy = bus.get_proxy_object("org.bluez", "/", introspection)
    manager = proxy.get_interface("org.bluez.AgentManager1")
    await manager.call_register_agent(agent_path, "KeyboardDisplay")
    await manager.call_request_default_agent(agent_path)
    receiver.pairing_agent_ready = True
    receiver.pairing_agent_error = None
    print("OmniPlayr Bluetooth pairing agent registered", flush=True)
    await asyncio.Event().wait()


receiver = Receiver()


class UnixRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        connection: socket.socket = self.request
        connection.settimeout(5)
        command = bytearray()
        try:
            while len(command) < 256 and b"\n" not in command:
                chunk = connection.recv(256 - len(command))
                if not chunk:
                    return
                command.extend(chunk)
        except (socket.timeout, OSError):
            return
        parts = bytes(command).split(b"\n", 1)[0].decode("utf-8", "replace").strip().split()
        action = parts[0].upper() if parts else ""
        address = parts[1] if len(parts) > 1 else None
        if action == "STREAM":
            connection.settimeout(None)
            receiver.stream_to(connection)
            return
        if action == "STATUS":
            response = receiver.status()
        elif action in {"PLAY", "PAUSE", "NEXT", "PREVIOUS"}:
            response = receiver.media_action(action.lower())
        elif action == "SCAN":
            response = receiver.start_scan()
        elif action == "PAIR" and address:
            response = receiver.pair(address)
        elif action in {"CONNECT", "DISCONNECT", "FORGET"}:
            response = receiver.device_action(action.lower(), address)
        else:
            response = {"ok": False, "error": "Unknown receiver command."}
        try:
            connection.sendall(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
        except OSError:
            pass


class PairingRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        connection: socket.socket = self.request
        connection.settimeout(5)
        payload = bytearray()
        try:
            while len(payload) < 8192 and b"\n" not in payload:
                chunk = connection.recv(8192 - len(payload))
                if not chunk:
                    return
                payload.extend(chunk)
            request = json.loads(bytes(payload).split(b"\n", 1)[0].decode("utf-8", "replace"))
        except (ValueError, socket.timeout, OSError):
            return
        if not isinstance(request, dict):
            response = {"ok": False, "error": "Invalid pairing request."}
        elif request.get("action") == "status":
            response = receiver.pairing_status()
        elif request.get("action") == "decision":
            response = receiver.pairing_decision(request)
        else:
            response = {"ok": False, "error": "Unknown pairing command."}
        try:
            connection.sendall(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
        except OSError:
            pass


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def start_pairing_agent() -> None:
    try:
        asyncio.run(run_pairing_agent())
    except Exception as exc:
        receiver.pairing_agent_ready = False
        receiver.pairing_agent_error = str(exc)
        print(f"OmniPlayr Bluetooth pairing agent failed: {exc}", flush=True)


def main() -> int:
    SOCKET_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
    os.chmod(SOCKET_DIR, 0o755)
    if SOCKET_PATH.exists() or SOCKET_PATH.is_symlink():
        mode = SOCKET_PATH.lstat().st_mode
        if not stat.S_ISSOCK(mode):
            raise RuntimeError(f"Refusing to remove non-socket path: {SOCKET_PATH}")
        SOCKET_PATH.unlink()
    if PAIRING_SOCKET_PATH.exists() or PAIRING_SOCKET_PATH.is_symlink():
        mode = PAIRING_SOCKET_PATH.lstat().st_mode
        if not stat.S_ISSOCK(mode):
            raise RuntimeError(f"Refusing to remove non-socket path: {PAIRING_SOCKET_PATH}")
        PAIRING_SOCKET_PATH.unlink()
    server = UnixServer(str(SOCKET_PATH), UnixRequestHandler)
    pairing_server = UnixServer(str(PAIRING_SOCKET_PATH), PairingRequestHandler)
    os.chmod(SOCKET_PATH, 0o660)
    os.chmod(PAIRING_SOCKET_PATH, 0o660)
    PID_PATH.write_text(str(os.getpid()), encoding="ascii")
    os.chmod(PID_PATH, 0o600)
    discovery_thread = threading.Thread(target=receiver.manage_discoverability, name="bluetooth-discoverability", daemon=True)
    discovery_thread.start()
    pairing_thread = threading.Thread(target=start_pairing_agent, name="bluetooth-pairing-agent", daemon=True)
    pairing_thread.start()
    pairing_server_thread = threading.Thread(target=pairing_server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
    pairing_server_thread.start()
    print(f"OmniPlayr Bluetooth receiver listening on {SOCKET_PATH}", flush=True)

    def stop(_signum: int, _frame: object) -> None:
        receiver.shutdown()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        receiver.shutdown()
        server.server_close()
        pairing_server.shutdown()
        pairing_server.server_close()
        try:
            SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass
        try:
            PAIRING_SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass
        try:
            PID_PATH.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
