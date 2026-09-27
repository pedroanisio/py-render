"""Audio decoding and writing.

decode() returns float32 (n, channels) at the requested sample rate and channel count:
PCM WAV files at the right rate are read with the `wave` module; everything else
(other rates, float WAV, mp3, m4a, flac, ogg, video containers) goes through the
ffmpeg binary shipped with imageio-ffmpeg, which also resamples (soxr-quality swr)
and down/up-mixes channels.
"""
from __future__ import annotations

import logging
import subprocess
import wave

import numpy as np

from ..registry import warn_once

log = logging.getLogger("scenerender")


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _read_wave(path: str):
    with wave.open(path, "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 1:
        a = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 2:
        a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif sw == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        a = v.astype(np.float32) / float(1 << 23)
    elif sw == 4:
        a = np.frombuffer(raw, "<i4").astype(np.float32) / float(1 << 31)
    else:
        raise ValueError(f"unsupported sample width {sw}")
    return a.reshape(-1, ch), sr


def _map_channels(a: np.ndarray, channels: int) -> np.ndarray:
    ch = a.shape[1]
    if ch == channels:
        return a
    if channels == 1:
        return a.mean(axis=1, keepdims=True)
    if ch == 1:
        return np.repeat(a, channels, axis=1)
    if channels == 2 and ch > 2:
        warn_once("audio", "downmix", "multichannel source downmixed to stereo (L+0.707C+0.707Ls / R+0.707C+0.707Rs)")
        L, R = a[:, 0], a[:, 1]
        C = a[:, 2] if ch > 2 else 0
        Ls = a[:, 4] if ch > 4 else 0
        Rs = a[:, 5] if ch > 5 else 0
        return np.stack([L + 0.7071 * C + 0.7071 * Ls, R + 0.7071 * C + 0.7071 * Rs], 1)
    return a[:, :channels]


def decode(path: str, sr: int, channels: int = 2, stream: int | None = None) -> np.ndarray:
    """Decode an audio file (or the audio stream `stream` of a video) to float32 (n, channels)."""
    if stream is None and path.lower().endswith((".wav", ".wave")):
        try:
            a, file_sr = _read_wave(path)
            if file_sr == sr:
                return np.ascontiguousarray(_map_channels(a, channels), np.float32)
        except (wave.Error, ValueError, EOFError):
            pass
    cmd = [ffmpeg_exe(), "-v", "error", "-nostdin", "-i", path]
    if stream is not None:
        cmd += ["-map", f"0:a:{int(stream)}"]
    cmd += ["-vn", "-f", "f32le", "-acodec", "pcm_f32le", "-ac", str(channels), "-ar", str(sr), "-"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise OSError(f"ffmpeg could not decode {path}: {p.stderr.decode(errors='replace').strip()[-400:]}")
    return np.frombuffer(p.stdout, np.float32).reshape(-1, channels).copy()


def write_wav(path: str, x: np.ndarray, sr: int, bits: int = 24, dither: bool = True, seed: int = 0,
              channel_mask: int | None = None) -> None:
    """Write float (n, ch) as integer PCM WAV (16/24/32-bit) with optional TPDF dither.
    More than two channels (or an explicit `channel_mask`) are written as WAVE_FORMAT_EXTENSIBLE
    with dwChannelMask = channel_mask (default: the ffmpeg default layout for the channel count;
    pass 0 for ambisonics / unassigned channels, see spatial.Layout.wav_mask)."""
    from .dsp import quantize
    from .spatial import AUTO, Layout
    bits = bits if bits in (16, 24, 32) else 24
    q = quantize(np.clip(x, -1.0, 1.0), bits, dither and bits < 32, seed)
    if bits == 16:
        raw = q.astype("<i2").tobytes()
    elif bits == 24:
        v = q.astype("<i4").reshape(-1)
        b = v.view(np.uint8).reshape(-1, 4)[:, :3]
        raw = np.ascontiguousarray(b).tobytes()
    else:
        raw = q.astype("<i4").tobytes()
    ch = int(x.shape[1])
    if channel_mask is None and ch <= 2:
        with wave.open(path, "wb") as w:
            w.setnchannels(ch)
            w.setsampwidth(bits // 8)
            w.setframerate(int(sr))
            w.writeframes(raw)
        return
    if channel_mask is None:
        channel_mask = Layout(AUTO[ch]).wav_mask if ch in AUTO else 0
    import struct
    block = ch * bits // 8
    guid_pcm = bytes.fromhex("0100000000001000800000aa00389b71")
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, ch, int(sr), int(sr) * block, block, bits, 22, bits,
                      int(channel_mask)) + guid_pcm
    data = raw + (b"\0" if len(raw) % 2 else b"")
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(raw)) + data
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", len(body)) + body)
