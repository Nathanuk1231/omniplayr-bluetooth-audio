import {
    api,
    getAccount,
    getVolumeStorage,
    type SourcePlugin,
    type TrackMetadata,
} from '@omniplayr/plugins';
import { getConfig } from '../../modules/config';

const SOURCE_TYPE = 'bluetooth-audio';
const LIVE_SONG_ID = 'live';
const VOLUME_STORAGE_KEY = 'player_volume';

export type BluetoothTrackMetadata = {
    title?: string | null;
    artist?: string | null;
    album?: string | null;
    album_art?: string | null;
    duration_ms?: number | null;
    position_ms?: number | null;
    status?: string | null;
    genre?: string | null;
    year?: string | number | null;
};

type BluetoothReceiverStatus = {
    bluetooth_connected?: boolean;
    device_name?: string | null;
    metadata?: BluetoothTrackMetadata | null;
};

function waitingMetadata(): TrackMetadata {
    return {
        title: 'Bluetooth Audio',
        artist: 'Waiting for track information',
        album: null,
        album_art: null,
        duration: 0,
        genre: 'Bluetooth audio',
        year: null,
        filename: null,
    };
}

export default class BluetoothAudioSourcePlugin implements SourcePlugin {
    private audio: HTMLAudioElement | null = null;
    private streamUrl: string | null = null;
    private callbacks: {
        onMetadata: (meta: TrackMetadata) => void;
        onReady: () => void;
        onEnded: () => void;
        onStateChange: () => void;
    } | null = null;
    private volume = 1;
    private playing = false;
    private destroyed = false;
    private metadataTimer: number | null = null;
    private lastMetadataKey = '';
    private positionMs = 0;
    private positionReceivedAt = Date.now();
    private durationSeconds = 0;
    private remoteStatus = 'unknown';
    private metadataMisses = 0;

    constructor() {
        const raw = getVolumeStorage()?.getItem(VOLUME_STORAGE_KEY) ?? null;
        const parsed = raw !== null ? Number.parseFloat(raw) : Number.NaN;
        if (Number.isFinite(parsed)) this.volume = Math.max(0, Math.min(1, parsed));
    }

    private buildStreamUrl(songId: string): string {
        const token = localStorage.getItem('access_token');
        const accountToken = getAccount();
        if (!token) throw new Error('OmniPlayr access token is not available');
        if (!accountToken) throw new Error('Select an OmniPlayr account before playing Bluetooth audio');
        const baseUrl = getConfig<string>('api.apiUrl') ?? '';
        const params = new URLSearchParams({ token, account_token: String(accountToken) });
        return `${baseUrl}/api/player/stream/${SOURCE_TYPE}:${encodeURIComponent(songId)}?${params.toString()}`;
    }

    private async remoteCommand(action: 'play' | 'pause' | 'next' | 'previous'): Promise<void> {
        await api(`/plugin/bluetooth-audio/control/${action}`, {});
    }

    async play(
        songId: string,
        _extra: Record<string, unknown> | undefined,
        autoplay: boolean,
        callbacks: {
            onMetadata: (meta: TrackMetadata) => void;
            onReady: () => void;
            onEnded: () => void;
            onStateChange: () => void;
        }
    ): Promise<void> {
        if (songId !== LIVE_SONG_ID) throw new Error('Unknown Bluetooth audio source');
        this.stopMetadataPolling();
        this.stopAudio();
        this.destroyed = false;
        this.callbacks = callbacks;
        this.streamUrl = this.buildStreamUrl(songId);
        this.lastMetadataKey = '';
        this.metadataMisses = 0;
        callbacks.onMetadata(waitingMetadata());
        await this.refreshMetadata();
        this.metadataTimer = window.setInterval(() => void this.refreshMetadata(), 1200);

        if (autoplay) {
            // Ask the connected phone to resume before attaching to the live A2DP stream.
            try {
                await this.remoteCommand('play');
            } catch {
                // Some phones stream immediately but do not expose AVRCP play control.
            }
            await new Promise(resolve => window.setTimeout(resolve, 300));
            await this.startAudio();
        } else {
            callbacks.onReady();
        }
    }

    private stopMetadataPolling(): void {
        if (this.metadataTimer !== null) {
            window.clearInterval(this.metadataTimer);
            this.metadataTimer = null;
        }
    }

    private async refreshMetadata(): Promise<void> {
        if (this.destroyed || !this.callbacks) return;
        try {
            const status = await api('/plugin/bluetooth-audio/status') as BluetoothReceiverStatus;
            if (!status.bluetooth_connected) {
                this.metadataMisses += 1;
                if (this.metadataMisses >= 3) {
                    if (this.audio) this.stopAudio();
                    this.positionMs = 0;
                    this.durationSeconds = 0;
                    this.remoteStatus = 'disconnected';
                    const waiting = waitingMetadata();
                    const key = JSON.stringify(waiting);
                    if (key !== this.lastMetadataKey) {
                        this.lastMetadataKey = key;
                        this.callbacks?.onMetadata(waiting);
                    }
                    this.callbacks?.onStateChange();
                }
                return;
            }
            if (!status.metadata) {
                this.metadataMisses += 1;
                if (this.metadataMisses >= 3) {
                    const waiting = waitingMetadata();
                    const key = JSON.stringify(waiting);
                    if (key !== this.lastMetadataKey) {
                        this.lastMetadataKey = key;
                        this.callbacks?.onMetadata(waiting);
                    }
                    this.callbacks?.onStateChange();
                }
                return;
            }
            this.metadataMisses = 0;
            const track = status.metadata;
            const durationMs = Number(track.duration_ms || 0);
            const position = Number(track.position_ms || 0);
            this.positionMs = Number.isFinite(position) ? Math.max(0, position) : 0;
            this.positionReceivedAt = Date.now();
            this.durationSeconds = durationMs > 0 ? durationMs / 1000 : 0;
            this.remoteStatus = String(track.status || 'unknown').toLowerCase();

            const metadata: TrackMetadata = {
                title: track.title?.trim() || 'Bluetooth Audio',
                artist: track.artist?.trim() || status.device_name || 'Bluetooth device',
                album: track.album?.trim() || null,
                album_art: track.album_art || null,
                duration: this.durationSeconds,
                genre: track.genre || 'Bluetooth audio',
                year: typeof track.year === 'number' ? track.year : (track.year ? Number(track.year) || null : null),
                filename: null,
            };
            const key = JSON.stringify(metadata);
            if (key !== this.lastMetadataKey) {
                this.lastMetadataKey = key;
                this.callbacks?.onMetadata(metadata);
            }
            this.callbacks?.onStateChange();
        } catch {
            // A metadata lookup failure must not interrupt the live audio stream.
        }
    }

    private async startAudio(): Promise<void> {
        if (this.destroyed) return;
        if (this.audio) {
            await this.audio.play();
            return;
        }
        if (!this.streamUrl) this.streamUrl = this.buildStreamUrl(LIVE_SONG_ID);
        const audio = new Audio();
        audio.preload = 'none';
        audio.volume = this.volume;
        this.audio = audio;
        audio.addEventListener('playing', () => {
            this.playing = true;
            this.callbacks?.onStateChange();
        });
        audio.addEventListener('pause', () => {
            this.playing = false;
            this.callbacks?.onStateChange();
        });
        audio.addEventListener('ended', () => {
            this.playing = false;
            this.callbacks?.onEnded();
            this.callbacks?.onStateChange();
        });
        audio.addEventListener('error', () => {
            this.playing = false;
            this.callbacks?.onStateChange();
        });
        audio.src = this.streamUrl;
        audio.load();
        try {
            await audio.play();
            this.playing = true;
            this.callbacks?.onReady();
            this.callbacks?.onStateChange();
        } catch (error) {
            this.stopAudio();
            throw error;
        }
    }

    pause(): void {
        this.stopAudio();
        void this.remoteCommand('pause').catch(() => {});
    }

    async resume(): Promise<void> {
        try {
            await this.remoteCommand('play');
        } catch {
            // Continue locally where AVRCP play is unavailable.
        }
        await new Promise(resolve => window.setTimeout(resolve, 250));
        await this.startAudio();
    }

    async nextTrack(): Promise<void> {
        await this.remoteCommand('next');
    }

    async previousTrack(): Promise<void> {
        await this.remoteCommand('previous');
    }

    seek(_seconds: number): void {
        // BlueZ MediaPlayer1 exposes Position as read-only on this receiver.
    }

    getCurrentTime(): number {
        const elapsed = this.remoteStatus === 'playing' ? (Date.now() - this.positionReceivedAt) / 1000 : 0;
        const position = this.positionMs / 1000 + elapsed;
        return this.durationSeconds > 0 ? Math.min(position, this.durationSeconds) : position;
    }

    getDuration(): number {
        return this.durationSeconds;
    }

    isPlaying(): boolean {
        if (this.remoteStatus === 'paused' || this.remoteStatus === 'stopped') return false;
        return this.playing;
    }

    setVolume(fraction: number): void {
        this.volume = Math.max(0, Math.min(1, fraction));
        getVolumeStorage()?.setItem(VOLUME_STORAGE_KEY, String(this.volume));
        if (this.audio) this.audio.volume = this.volume;
    }

    setTransientVolume(fraction: number): void {
        if (this.audio) this.audio.volume = Math.max(0, Math.min(1, fraction));
    }

    activate(): void {}

    destroy(_options?: { transferred?: boolean }): void {
        this.destroyed = true;
        this.stopMetadataPolling();
        this.stopAudio();
        this.callbacks = null;
        this.streamUrl = null;
    }

    private stopAudio(): void {
        const audio = this.audio;
        this.audio = null;
        this.playing = false;
        if (audio) {
            audio.pause();
            audio.removeAttribute('src');
            audio.load();
        }
        this.callbacks?.onStateChange();
    }
}
