import { useCallback, useEffect, useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import { api, playSong } from '@omniplayr/plugins';
import {
    Bluetooth,
    Headphones,
    Play,
    RefreshCw,
    Radio,
    Smartphone,
    ScanSearch,
    Unplug,
    Trash2,
    LockKeyhole,
    X,
    Check,
    Music2,
} from 'lucide-react';
import { player } from '../../modules/player';

type Device = { address: string; name: string };
type Track = {
    title?: string | null;
    artist?: string | null;
    album?: string | null;
    album_art?: string | null;
    duration_ms?: number | null;
};
type PairingRequest = {
    id: string;
    type: 'enter_pin' | 'enter_passkey' | 'confirm' | 'display_pin' | 'display_passkey' | 'authorize' | 'authorize_service';
    device_name: string;
    address: string;
    passkey?: string | null;
    entered?: number | null;
    service_uuid?: string | null;
    display_only: boolean;
    message: string;
    expires_at: number;
};
type ReceiverStatus = {
    online: boolean;
    host_platform?: string;
    host_supported?: boolean;
    host_ready?: boolean;
    host_error?: string | null;
    bootstrap_error?: string | null;
    missing_tools?: string[];
    bluetooth_connected: boolean;
    device_name: string | null;
    connected_devices: Device[];
    paired_devices: Device[];
    devices: Device[];
    audio_streaming: boolean;
    listeners: number;
    capture_running: boolean;
    capture_error?: string | null;
    discoverable?: boolean;
    pairable?: boolean;
    metadata?: Track | null;
    pair_job?: { state: string; message?: string; address?: string };
    pairing?: { online: boolean; pairing_request?: PairingRequest | null; error?: string };
    error?: string;
};

const initialStatus: ReceiverStatus = {
    online: false,
    bluetooth_connected: false,
    device_name: null,
    connected_devices: [],
    paired_devices: [],
    devices: [],
    audio_streaming: false,
    listeners: 0,
    capture_running: false,
};

const styles: Record<string, CSSProperties> = {
    page: { display: 'flex', flexDirection: 'column', gap: 18, padding: 24, maxWidth: 850, margin: '0 auto', color: 'var(--clr-text-a0, inherit)' },
    header: { display: 'flex', alignItems: 'center', gap: 14 },
    icon: { width: 48, height: 48, borderRadius: 14, display: 'grid', placeItems: 'center', background: 'var(--clr-surface-tonal-a0)', color: 'var(--clr-primary-a0)' },
    title: { margin: 0, fontSize: 22, fontWeight: 700 },
    subtitle: { margin: '4px 0 0', color: 'var(--clr-text-a30)', fontSize: 13 },
    panel: { padding: 18, borderRadius: 14, border: '1px solid var(--border, var(--clr-surface-a20))', background: 'var(--clr-surface-a0)' },
    sectionTitle: { margin: '0 0 10px', fontSize: 15, fontWeight: 650 },
    row: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, padding: '10px 0', borderBottom: '1px solid var(--border, var(--clr-surface-a20))', fontSize: 13 },
    label: { display: 'flex', alignItems: 'center', gap: 9, color: 'var(--clr-text-a20)' },
    value: { fontSize: 12, fontWeight: 650, textAlign: 'right', overflowWrap: 'anywhere' },
    button: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 9, border: 0, borderRadius: 10, padding: '11px 15px', cursor: 'pointer', fontWeight: 650, fontSize: 13, background: 'var(--clr-primary-a0)', color: 'var(--clr-text-white-a0, #fff)' },
    secondary: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8, border: '1px solid var(--border, var(--clr-surface-a20))', borderRadius: 10, padding: '10px 13px', cursor: 'pointer', background: 'var(--clr-surface-a10)', color: 'var(--clr-text-a0)', fontSize: 12, fontWeight: 600 },
    danger: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8, border: '1px solid var(--clr-danger-a20)', borderRadius: 10, padding: '10px 13px', cursor: 'pointer', background: 'transparent', color: 'var(--clr-danger-a0)', fontSize: 12, fontWeight: 600 },
    muted: { color: 'var(--clr-text-a30)', fontSize: 12, lineHeight: 1.5 },
    actions: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 9, marginTop: 14 },
    select: { width: '100%', minWidth: 0, padding: '10px 12px', borderRadius: 9, border: '1px solid var(--border, var(--clr-surface-a20))', background: 'var(--clr-surface-a10)', color: 'var(--clr-text-a0)', fontSize: 13 },
    track: { display: 'flex', alignItems: 'center', gap: 14, minWidth: 0 },
    cover: { width: 72, height: 72, flexShrink: 0, borderRadius: 9, objectFit: 'cover', background: 'var(--clr-surface-tonal-a0)' },
    trackTitle: { fontSize: 14, fontWeight: 650, marginBottom: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
    trackMeta: { color: 'var(--clr-text-a30)', fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
    alert: { margin: '12px 0 0', padding: '10px 12px', borderRadius: 9, background: 'var(--clr-danger-a10)', color: 'var(--clr-danger-a0)', fontSize: 12, lineHeight: 1.5 },
    success: { margin: '12px 0 0', padding: '10px 12px', borderRadius: 9, background: 'var(--clr-success-a10)', color: 'var(--clr-success-a0)', fontSize: 12, lineHeight: 1.5 },
    modal: { position: 'fixed', inset: 0, zIndex: 10000, display: 'grid', placeItems: 'center', padding: 18, background: 'rgba(0,0,0,.62)' },
    modalCard: { width: 'min(100%, 420px)', padding: 22, borderRadius: 16, border: '1px solid var(--border, var(--clr-surface-a20))', background: 'var(--clr-surface-a0)', boxShadow: '0 20px 80px rgba(0,0,0,.35)' },
    modalCode: { fontSize: 30, fontWeight: 750, letterSpacing: 5, textAlign: 'center', padding: '14px 8px', margin: '14px 0', borderRadius: 10, background: 'var(--clr-surface-tonal-a0)', color: 'var(--clr-primary-a0)' },
    input: { width: '100%', boxSizing: 'border-box', padding: '12px 13px', borderRadius: 9, border: '1px solid var(--border, var(--clr-surface-a20))', background: 'var(--clr-surface-a10)', color: 'var(--clr-text-a0)', fontSize: 16 },
};

export default function BluetoothAudioTab() {
    const [status, setStatus] = useState<ReceiverStatus>(initialStatus);
    const [loading, setLoading] = useState(true);
    const [working, setWorking] = useState('');
    const [selectedAddress, setSelectedAddress] = useState('');
    const [message, setMessage] = useState('');
    const [error, setError] = useState('');
    const [pairValue, setPairValue] = useState('');

    const refresh = useCallback(async () => {
        try {
            const result = await api('/plugin/bluetooth-audio/status') as ReceiverStatus;
            setStatus(result);
            setError(result.online ? '' : (result.error || 'Receiver service is not running'));
            setSelectedAddress(current => {
                if (current && result.devices?.some(device => device.address === current)) return current;
                return result.connected_devices?.[0]?.address || result.paired_devices?.[0]?.address || result.devices?.[0]?.address || '';
            });
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : 'Could not contact the Bluetooth receiver');
            setStatus(initialStatus);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void refresh();
        const timer = window.setInterval(() => void refresh(), 1500);
        return () => window.clearInterval(timer);
    }, [refresh]);

    const selectedDevice = useMemo(() => status.devices?.find(device => device.address === selectedAddress), [status.devices, selectedAddress]);
    const isSelectedPaired = status.paired_devices?.some(device => device.address === selectedAddress) ?? false;
    const pairingRequest = status.pairing?.pairing_request ?? null;

    const control = async (action: string, address?: string) => {
        setWorking(action);
        setError('');
        setMessage('');
        try {
            const result = await api(`/plugin/bluetooth-audio/control/${action}`, address ? { address } : {}) as { message?: string };
            setMessage(result?.message || 'Bluetooth request completed.');
            await refresh();
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : `Bluetooth ${action} failed`);
        } finally {
            setWorking('');
        }
    };

    const disconnect = async () => {
        const connectedAddress = status.connected_devices?.[0]?.address;
        await control('disconnect', connectedAddress);
        if (player.currentSourceType === 'bluetooth-audio') player.stopBluetoothPlayback();
    };

    const startPlayback = async () => {
        setWorking('play');
        setError('');
        try {
            await playSong('live', 'bluetooth-audio');
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : 'Could not start Bluetooth audio');
        } finally {
            setWorking('');
        }
    };

    const submitPairingDecision = async (decision: 'accept' | 'reject' | 'submit') => {
        if (!pairingRequest) return;
        try {
            await api('/plugin/bluetooth-audio/pairing/decision', {
                id: pairingRequest.id,
                decision,
                ...(decision === 'submit' ? { value: pairValue } : {}),
            });
            setPairValue('');
            setMessage(decision === 'reject' ? 'Pairing rejected.' : 'Pairing response sent.');
            await refresh();
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : 'Could not send pairing response');
        }
    };

    const pairedText = status.paired_devices?.length ? `${status.paired_devices.length} paired` : 'No paired devices';
    const track = status.metadata;

    return (
        <div style={styles.page}>
            <header style={styles.header}>
                <div style={styles.icon}><Bluetooth size={27} /></div>
                <div>
                    <h2 style={styles.title}>Bluetooth Audio</h2>
                    <p style={styles.subtitle}>Connect a device and play its live audio through OmniPlayr.</p>
                </div>
            </header>

            <section style={styles.panel}>
                <h3 style={styles.sectionTitle}>Receiver status</h3>
                <div style={styles.row}>
                    <span style={styles.label}><Radio size={17} /> Receiver service</span>
                    <strong style={styles.value}>{loading ? 'Checking…' : status.online ? 'Online' : 'Offline'}</strong>
                </div>
                <div style={styles.row}>
                    <span style={styles.label}><Smartphone size={17} /> Connected device</span>
                    <strong style={styles.value}>{status.bluetooth_connected ? (status.device_name || 'Connected') : 'Not connected'}</strong>
                </div>
                <div style={styles.row}>
                    <span style={styles.label}><Headphones size={17} /> Audio stream</span>
                    <strong style={styles.value}>{status.audio_streaming ? 'Audio detected' : 'Waiting for audio'}</strong>
                </div>
                <div style={styles.row}>
                    <span style={styles.label}><Bluetooth size={17} /> Server host</span>
                    <strong style={styles.value}>{status.host_platform || 'Checking…'}</strong>
                </div>
                <div style={styles.row}>
                    <span style={styles.label}><Bluetooth size={17} /> Host compatibility</span>
                    <strong style={styles.value}>{loading ? 'Checking…' : status.host_ready ? 'Ready' : status.host_supported ? 'Needs setup' : 'Unsupported'}</strong>
                </div>
                <div style={{ ...styles.row, borderBottom: 0 }}>
                    <span style={styles.label}><LockKeyhole size={17} /> Pairing agent</span>
                    <strong style={styles.value}>{loading ? 'Checking…' : status.pairing?.online ? 'Ready' : 'Unavailable'}</strong>
                </div>
                {!loading && !status.host_ready && (status.host_error || status.bootstrap_error) && (
                    <div style={styles.alert}>{status.host_error || status.bootstrap_error}</div>
                )}
            </section>

            <section style={styles.panel}>
                <h3 style={styles.sectionTitle}>Devices</h3>
                <p style={styles.muted}>The server becomes discoverable automatically when no device is connected. Any phone, tablet or computer that supports Bluetooth audio streaming can connect.</p>
                <div style={{ marginTop: 13 }}>
                    <select style={styles.select} value={selectedAddress} onChange={event => setSelectedAddress(event.target.value)}>
                        <option value="">Select a device…</option>
                        {(status.devices || []).map(device => (
                            <option key={device.address} value={device.address}>{device.name} ({device.address})</option>
                        ))}
                    </select>
                </div>
                {selectedDevice && <p style={styles.muted}>{isSelectedPaired ? 'Already paired' : 'Not yet paired'} · {selectedDevice.name}</p>}
                <div style={styles.actions}>
                    <button type="button" style={styles.secondary} disabled={!!working || status.bluetooth_connected} onClick={() => void control('scan')}>
                        <ScanSearch size={16} /> {working === 'scan' ? 'Scanning…' : 'Scan for devices'}
                    </button>
                    <button type="button" style={styles.button} disabled={!!working || !selectedAddress || isSelectedPaired || status.bluetooth_connected} onClick={() => void control('pair', selectedAddress)}>
                        <Bluetooth size={16} /> {working === 'pair' ? 'Pairing…' : 'Pair'}
                    </button>
                    <button type="button" style={styles.secondary} disabled={!!working || !selectedAddress || !isSelectedPaired || status.bluetooth_connected} onClick={() => void control('connect', selectedAddress)}>
                        <Bluetooth size={16} /> Connect
                    </button>
                    <button type="button" style={styles.secondary} disabled={!!working || !status.bluetooth_connected} onClick={() => void disconnect()}>
                        <Unplug size={16} /> Disconnect
                    </button>
                    <button type="button" style={styles.danger} disabled={!!working || !selectedAddress || !isSelectedPaired} onClick={async () => {
                        await control('forget', selectedAddress);
                        if (player.currentSourceType === 'bluetooth-audio') player.stopBluetoothPlayback();
                    }}>
                        <Trash2 size={15} /> Forget
                    </button>
                </div>
                <p style={{ ...styles.muted, marginTop: 11 }}>{pairedText}. Only one Bluetooth device can be connected at a time.</p>
            </section>

            <section style={styles.panel}>
                <h3 style={styles.sectionTitle}>Now playing</h3>
                <div style={styles.track}>
                    {track?.album_art ? (
                        <img src={track.album_art} alt="Album cover" style={styles.cover} />
                    ) : (
                        <div style={{ ...styles.cover, display: 'grid', placeItems: 'center', color: 'var(--clr-text-a30)' }}><Music2 size={27} /></div>
                    )}
                    <div style={{ minWidth: 0, flex: 1 }}>
                        <div style={styles.trackTitle}>{track?.title || 'No track information yet'}</div>
                        <div style={styles.trackMeta}>{[track?.artist, track?.album].filter(Boolean).join(' · ') || (status.bluetooth_connected ? 'Waiting for phone metadata' : 'Connect a Bluetooth device')}</div>
                        {track && <div style={{ ...styles.trackMeta, marginTop: 4 }}>{track.duration_ms ? `${Math.floor(track.duration_ms / 60000)}:${String(Math.floor(track.duration_ms / 1000) % 60).padStart(2, '0')}` : 'Live audio'}</div>}
                    </div>
                </div>
            </section>

            <section style={styles.panel}>
                <h3 style={styles.sectionTitle}>Live playback</h3>
                <p style={styles.muted}>Pair and connect your phone, start playing audio on it, then start the live stream here. Pause, next and previous are available in OmniPlayr's normal player controls.</p>
                <div style={styles.actions}>
                    <button type="button" style={{ ...styles.button, opacity: (!status.bluetooth_connected || !status.audio_streaming || !!working) ? .5 : 1 }} disabled={!status.bluetooth_connected || !status.audio_streaming || !!working} onClick={() => void startPlayback()}>
                        <Play size={17} /> {working === 'play' ? 'Starting…' : 'Play live audio'}
                    </button>
                    <button type="button" style={styles.secondary} disabled={loading || !!working} onClick={() => void refresh()}>
                        <RefreshCw size={16} /> Refresh status
                    </button>
                </div>
                {status.online && !status.audio_streaming && <p style={styles.muted}>The server is connected, but an active A2DP stream has not been detected yet. Start music on your device.</p>}
                {status.capture_error && <p role="alert" style={styles.alert}>Audio capture: {status.capture_error}</p>}
                {status.pairing && !status.pairing.online && status.pairing.error && <p role="alert" style={styles.alert}>Pairing agent: {status.pairing.error}</p>}
                {status.pair_job?.message && status.pair_job.state !== 'idle' && <p style={status.pair_job.state === 'failed' ? styles.alert : styles.muted}>{status.pair_job.message}</p>}
                {message && <p role="status" style={styles.success}>{message}</p>}
                {error && <p role="alert" style={styles.alert}>{error}</p>}
            </section>

            {pairingRequest && (
                <div style={styles.modal} role="presentation">
                    <div style={styles.modalCard} role="dialog" aria-modal="true" aria-labelledby="bluetooth-pair-title">
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                            <h3 id="bluetooth-pair-title" style={{ margin: 0, fontSize: 17 }}>Bluetooth pairing</h3>
                            {!pairingRequest.display_only && <LockKeyhole size={19} />}
                        </div>
                        <p style={styles.muted}>{pairingRequest.device_name} · {pairingRequest.message}</p>
                        {(pairingRequest.type === 'display_passkey' || pairingRequest.type === 'display_pin' || pairingRequest.type === 'confirm') && (
                            <div style={styles.modalCode}>{pairingRequest.passkey || '—'}</div>
                        )}
                        {pairingRequest.type === 'confirm' && (
                            <p style={styles.muted}>Check that this code matches the one shown on your device.</p>
                        )}
                        {(pairingRequest.type === 'enter_pin' || pairingRequest.type === 'enter_passkey') && (
                            <input autoFocus style={styles.input} value={pairValue} onChange={event => setPairValue(event.target.value)} placeholder={pairingRequest.type === 'enter_pin' ? 'Enter PIN' : 'Enter 6-digit passkey'} inputMode="numeric" />
                        )}
                        <div style={styles.actions}>
                            {pairingRequest.type === 'confirm' || pairingRequest.type === 'authorize' || pairingRequest.type === 'authorize_service' ? (
                                <>
                                    <button type="button" style={styles.button} onClick={() => void submitPairingDecision('accept')}><Check size={15} /> Allow</button>
                                    <button type="button" style={styles.danger} onClick={() => void submitPairingDecision('reject')}><X size={15} /> Reject</button>
                                </>
                            ) : null}
                            {pairingRequest.type === 'enter_pin' || pairingRequest.type === 'enter_passkey' ? (
                                <>
                                    <button type="button" style={styles.button} disabled={!pairValue.trim()} onClick={() => void submitPairingDecision('submit')}><Check size={15} /> Submit code</button>
                                    <button type="button" style={styles.danger} onClick={() => void submitPairingDecision('reject')}><X size={15} /> Cancel</button>
                                </>
                            ) : null}
                            {pairingRequest.display_only && <button type="button" style={styles.button} onClick={() => void refresh()}><Check size={15} /> Code displayed</button>}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
