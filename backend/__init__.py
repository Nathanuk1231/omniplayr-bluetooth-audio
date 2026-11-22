"""OmniPlayr Bluetooth A2DP/AVRCP source plugin (optional local extension)."""
from __future__ import annotations

import json
import os
import platform
import socket
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterator

from fastapi import Body, Depends, HTTPException

from omniplayr.plugins import PluginBase, api, register, verify_auth

PLUGIN_ID = "bluetooth-audio@Nathanuk1231"
SOURCE_TYPE = "bluetooth-audio"
PROJECT_DIR = Path(os.environ.get("OMNIPLAYR_PROJECT_DIR", "/compose")).expanduser().resolve()
RUNTIME_DIR = Path(os.environ.get("OMNIPLAYR_RUNTIME_DIR", str(PROJECT_DIR / ".runtime"))).expanduser().resolve()
SOCKET_DIR = RUNTIME_DIR / "bluetooth-audio"
SOCKET_PATH = SOCKET_DIR / "receiver.sock"
PAIRING_SOCKET_PATH = SOCKET_DIR / "pairing.sock"
RECEIVER_SCRIPT = Path(__file__).with_name("receiver.py")
PID_PATH = SOCKET_DIR / "receiver.pid"
LOG_PATH = SOCKET_DIR / "receiver.log"
_REQUIRED_COMMANDS = ("bluetoothctl", "busctl", "pw-dump", "pw-record", "ffmpeg")
_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAP_ERROR: str | None = None
_ART_CACHE: dict[str, tuple[float, dict]] = {}


def _host_diagnostics() -> dict:
    host = platform.system()
    in_container = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    missing = [command for command in _REQUIRED_COMMANDS if not shutil.which(command)] if host == "Linux" else list(_REQUIRED_COMMANDS)
    dbus_socket = Path(os.environ.get("OMNIPLAYR_DBUS_SOCKET", "/run/dbus/system_bus_socket"))
    dbus_available = dbus_socket.exists()
    supported = host == "Linux"
    if not supported:
        error = f"The Bluetooth receiver host currently requires Linux. Detected: {host or 'unknown'}."
    elif missing:
        error = "Missing host tools: " + ", ".join(missing)
    elif not dbus_available:
        error = f"The system D-Bus socket is unavailable at {dbus_socket}. The host Bluetooth service is not exposed to OmniPlayr."
    else:
        error = None
    return {
        "host_platform": "Linux container (underlying host not detectable)" if in_container else (host or "unknown"),
        "host_supported": supported,
        "missing_tools": missing,
        "dbus_available": dbus_available,
        "host_error": error,
    }


def _process_matches(pid: int) -> bool:
    command_path = Path(f"/proc/{pid}/cmdline")
    try:
        command_line = command_path.read_bytes()
    except OSError:
        return False
    return str(RECEIVER_SCRIPT).encode() in command_line


def _receiver_responds() -> bool:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(0.35)
    try:
        client.connect(str(SOCKET_PATH))
        return True
    except OSError:
        return False
    finally:
        client.close()


def _start_receiver() -> None:
    global _BOOTSTRAP_ERROR
    with _BOOTSTRAP_LOCK:
        diagnostics = _host_diagnostics()
        if not diagnostics["host_supported"]:
            _BOOTSTRAP_ERROR = diagnostics["host_error"]
            return
        if _receiver_responds():
            _BOOTSTRAP_ERROR = None
            return
        try:
            old_pid = int(PID_PATH.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            old_pid = 0
        if old_pid and _process_matches(old_pid):
            _BOOTSTRAP_ERROR = None
            return
        if not RECEIVER_SCRIPT.is_file():
            _BOOTSTRAP_ERROR = f"Bundled receiver script was not found: {RECEIVER_SCRIPT}"
            return
        try:
            SOCKET_DIR.mkdir(parents=True, exist_ok=True)
            environment = os.environ.copy()
            environment["OMNIPLAYR_RUNTIME_DIR"] = str(RUNTIME_DIR)
            environment["OMNIPLAYR_PROJECT_DIR"] = str(PROJECT_DIR)
            with LOG_PATH.open("ab", buffering=0) as log_file:
                process = subprocess.Popen(
                    [sys.executable, str(RECEIVER_SCRIPT)],
                    cwd=str(RECEIVER_SCRIPT.parent),
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    env=environment,
                    start_new_session=True,
                    close_fds=True,
                )
            PID_PATH.write_text(str(process.pid), encoding="ascii")
            os.chmod(PID_PATH, stat.S_IRUSR | stat.S_IWUSR)
            _BOOTSTRAP_ERROR = None
        except (OSError, subprocess.SubprocessError) as exc:
            _BOOTSTRAP_ERROR = f"Could not start the bundled Bluetooth receiver: {exc}"


def _socket_json(path: Path, payload: bytes, timeout: float = 5.0) -> dict:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
        client.sendall(payload + b"\n")
        response = bytearray()
        while len(response) < 65536:
            chunk = client.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
            if b"\n" in response:
                break
        if not response:
            raise ConnectionError("Bluetooth service returned an empty response")
        result = json.loads(bytes(response).split(b"\n", 1)[0].decode("utf-8"))
        return result if isinstance(result, dict) else {}
    finally:
        client.close()


def _service_request(command: bytes, timeout: float = 6.0) -> dict:
    return _socket_json(SOCKET_PATH, command, timeout)


def _pairing_request(payload: dict) -> dict:
    return _socket_json(PAIRING_SOCKET_PATH, json.dumps(payload, separators=(",", ":")).encode("utf-8"), 4.0)


def _normalise(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _lookup_artwork(metadata: dict) -> dict:
    title = str(metadata.get("title") or "").strip()
    artist = str(metadata.get("artist") or "").strip()
    if not title or not artist:
        return metadata
    key = f"{_normalise(title)}|{_normalise(artist)}"
    now = time.time()
    cached = _ART_CACHE.get(key)
    if cached and cached[0] > now:
        enriched = dict(metadata)
        for name, value in cached[1].items():
            if value and not enriched.get(name):
                enriched[name] = value
        return enriched

    found: dict = {}
    try:
        query = urllib.parse.urlencode({"term": f"{title} {artist}", "entity": "song", "limit": 8})
        request = urllib.request.Request(
            f"https://itunes.apple.com/search?{query}",
            headers={"User-Agent": "OmniPlayr-Bluetooth/1.0"},
        )
        with urllib.request.urlopen(request, timeout=2.5) as response:
            payload = json.loads(response.read(512_000).decode("utf-8", "replace"))
        candidates = payload.get("results", []) if isinstance(payload, dict) else []
        for item in candidates:
            if _normalise(item.get("trackName")) != _normalise(title):
                continue
            if _normalise(item.get("artistName")) != _normalise(artist):
                continue
            artwork = item.get("artworkUrl100") or item.get("artworkUrl60")
            if artwork:
                artwork = artwork.replace("100x100bb", "600x600bb").replace("60x60bb", "600x600bb")
                found["album_art"] = artwork
            if item.get("collectionName"):
                found["album"] = item["collectionName"]
            if item.get("primaryGenreName"):
                found["genre"] = item["primaryGenreName"]
            if item.get("releaseDate"):
                found["year"] = str(item["releaseDate"])[:4]
            if item.get("trackTimeMillis"):
                found["duration"] = float(item["trackTimeMillis"]) / 1000
            break
    except (OSError, ValueError, TypeError, TimeoutError):
        pass

    # Successful matches live for 30 minutes; misses for 3 minutes.
    _ART_CACHE[key] = (now + (1800 if found else 180), found)
    enriched = dict(metadata)
    for name, value in found.items():
        if value and not enriched.get(name):
            enriched[name] = value
    return enriched


def get_receiver_status() -> dict:
    _start_receiver()
    diagnostics = _host_diagnostics()
    try:
        status = _service_request(b"STATUS", timeout=7.0)
        status["online"] = True
        status["host_platform"] = diagnostics["host_platform"]
        status["host_supported"] = diagnostics["host_supported"]
        status["missing_tools"] = diagnostics["missing_tools"]
        status["dbus_available"] = diagnostics["dbus_available"]
        if not status.get("host_error"):
            status["host_error"] = diagnostics["host_error"]
        try:
            status["pairing"] = _pairing_request({"action": "status"})
        except (OSError, ValueError, TimeoutError, ConnectionError) as pairing_error:
            status["pairing"] = {"online": False, "error": str(pairing_error), "pairing_request": None}
        if _BOOTSTRAP_ERROR:
            status["bootstrap_error"] = _BOOTSTRAP_ERROR
        metadata = status.get("metadata")
        if isinstance(metadata, dict):
            status["metadata"] = _lookup_artwork(metadata)
        return status
    except (OSError, ValueError, TimeoutError, ConnectionError) as exc:
        return {
            "online": False,
            "bluetooth_connected": False,
            "device_name": None,
            "connected_devices": [],
            "paired_devices": [],
            "devices": [],
            "audio_streaming": False,
            "metadata": None,
            "listeners": 0,
            "capture_running": False,
            "error": _BOOTSTRAP_ERROR or str(exc),
            "host_ready": False,
            **diagnostics,
        }


class BluetoothAudioPlugin(PluginBase):
    source_type = SOURCE_TYPE

    def check_ownership(self, song_id: str, account_id: int) -> bool:
        return song_id == "live" and SOCKET_PATH.exists()

    def get_content_type(self, song_id: str, account_id: int) -> str:
        return "audio/mpeg"

    def get_file_size(self, song_id: str, account_id: int) -> int | None:
        return None

    def get_metadata(self, song_id: str, account_id: int) -> dict:
        if song_id != "live":
            raise FileNotFoundError(f"Unknown Bluetooth audio source: {song_id}")
        status = get_receiver_status()
        track = status.get("metadata") or {}
        duration_ms = track.get("duration_ms") or 0
        try:
            duration = max(0.0, float(duration_ms) / 1000)
        except (TypeError, ValueError):
            duration = 0.0
        if not duration:
            duration = float(track.get("duration") or 0)
        return {
            "title": track.get("title") or "Bluetooth Audio",
            "artist": track.get("artist") or status.get("device_name") or "Bluetooth device",
            "album": track.get("album") or None,
            "album_art": track.get("album_art") or None,
            "duration": duration,
            "genre": track.get("genre") or "Bluetooth audio",
            "year": track.get("year") or None,
            "filename": None,
        }

    def get_stream(self, song_id: str, account_id: int) -> Iterator[bytes]:
        if song_id != "live":
            raise FileNotFoundError(f"Unknown Bluetooth audio source: {song_id}")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(30.0)
        try:
            client.connect(str(SOCKET_PATH))
            client.sendall(b"STREAM\n")
        except OSError as exc:
            client.close()
            raise FileNotFoundError("Bluetooth receiver service is not available") from exc

        def stream_chunks() -> Iterator[bytes]:
            try:
                while True:
                    try:
                        chunk = client.recv(8192)
                    except socket.timeout:
                        continue
                    if not chunk:
                        return
                    yield chunk
            finally:
                client.close()
        return stream_chunks()


@api.get("/bluetooth-audio/status")
def bluetooth_audio_status(_auth=Depends(verify_auth)):
    return get_receiver_status()


@api.post("/bluetooth-audio/control/{action}")
def bluetooth_audio_control(action: str, payload: dict = Body(default={}), _auth=Depends(verify_auth)):
    if action not in {"scan", "pair", "connect", "disconnect", "forget", "play", "pause", "next", "previous"}:
        raise HTTPException(status_code=404, detail="Unknown Bluetooth action")
    address = str(payload.get("address") or "").strip().upper()
    command = action.upper() + (f" {address}" if address and action in {"pair", "connect", "disconnect", "forget"} else "")
    try:
        result = _service_request(command.encode("ascii"), timeout=6.0)
    except (OSError, ValueError, TimeoutError, ConnectionError) as exc:
        raise HTTPException(status_code=503, detail=f"Bluetooth receiver unavailable: {exc}") from exc
    if not result.get("ok", True):
        raise HTTPException(status_code=409, detail=result.get("error") or "Bluetooth action failed")
    return result


@api.post("/bluetooth-audio/pairing/decision")
def bluetooth_audio_pairing_decision(payload: dict = Body(...), _auth=Depends(verify_auth)):
    try:
        result = _pairing_request({
            "action": "decision",
            "id": str(payload.get("id", "")),
            "decision": str(payload.get("decision", "")),
            "value": payload.get("value"),
        })
    except (OSError, ValueError, TimeoutError, ConnectionError) as exc:
        raise HTTPException(status_code=503, detail=f"Bluetooth pairing agent unavailable: {exc}") from exc
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error") or "Pairing request expired")
    return result


def setup() -> None:
    register(BluetoothAudioPlugin())
    _start_receiver()
