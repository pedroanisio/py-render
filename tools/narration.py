# v2 narration: one neural-TTS clip per caption cue, silence-trimmed, sped up only if it would not fit before the next cue,
# placed 50 ms after the cue start, then processed (high-pass, presence EQ, compression) into a stereo stem.
# Requires edge-tts; in the sandbox the system CA bundle had to be appended to certifi's cacert.pem first.
import subprocess, numpy as np, sys
from scipy.io import wavfile
sys.path.insert(0, '/tmp/sb'); import sr
SR = 48000; N = 90 * SR; vo = np.zeros(N)
for i, (a, b, t) in enumerate(sr.CUES):
    subprocess.run(['edge-tts', '--voice', 'en-US-AndrewNeural', '--rate', '+4%', '--text', t, '--write-media', f'vo/c{i:02d}.mp3'], check=True, capture_output=True)
starts = [c[0] for c in sr.CUES] + [89.85]
for i, (a, b, t) in enumerate(sr.CUES):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', f'vo/c{i:02d}.mp3', '-af',
                    'silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse',
                    '-ar', str(SR), '-ac', '1', f'vo/t{i:02d}.wav'], check=True)
    _, x = wavfile.read(f'vo/t{i:02d}.wav'); d = len(x) / SR
    avail = min(b + 0.35, starts[i + 1] - 0.12) - a
    tempo = max(1.0, d / avail)                     # in the delivered mix every line fitted at 1.0
    if tempo > 1.0:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', f'vo/t{i:02d}.wav', '-af', f'atempo={tempo:.4f}', '-ar', str(SR), f'vo/f{i:02d}.wav'], check=True)
        _, x = wavfile.read(f'vo/f{i:02d}.wav')
    x = x.astype(np.float64) / 32768
    s = int((a + 0.05) * SR); vo[s:s + len(x)] += x[:N - s]
wavfile.write('vo/narration_mono.wav', SR, vo.astype(np.float32))
subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', 'vo/narration_mono.wav', '-af',
                'highpass=f=80,equalizer=f=3000:t=q:w=1:g=2,acompressor=threshold=-20dB:ratio=3:attack=5:release=80:makeup=2',
                '-ac', '2', '-ar', '48000', '-c:a', 'pcm_s24le', 'vo/narration.wav'], check=True)
