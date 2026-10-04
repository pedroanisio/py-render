"""Rebuild tiny BasisU fixtures with Khronos KTX-Software 4.4.2.

Usage: python generate.py /path/to/ktx
Inputs are generated here; CLI-extracted RGBA8 pixels are the decoder oracle.
The deliberately different mip colors detect accidental mip regeneration.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile

import numpy as np


def generate(executable):
    root = Path(__file__).resolve().parent
    arrays, records = {}, []
    specs = [(codec, channels, color, 4, '') for codec in ('etc1s', 'uastc', 'uastc_zstd')
             for channels, color in ((1, False), (2, False), (3, False), (4, False), (3, True), (4, True))]
    specs += [('uastc_zstd', 4, True, 1, '_single'), ('etc1s', 4, False, 2, '_partial'),
              ('uastc', 2, False, 4, '_legacy')]
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        for codec, channels, color, count, suffix in specs:
            name = f'{codec}_{channels}_{"color" if color else "data"}'+suffix
            inputs = []
            for level in range(count):
                h, w = max(1, 8 >> level), max(1, 12 >> level)
                y, x = np.mgrid[:h, :w]
                rgba = np.empty((h, w, 4), np.uint8)
                rgba[..., 0] = (24+65*(x//4)+37*level) % 256
                rgba[..., 1] = (58+83*(y//4)+31*level) % 256
                rgba[..., 2] = (212-19*level) % 256
                rgba[..., 3] = (220-48*(x//4)-22*level) % 256
                source = np.ascontiguousarray(rgba[..., :channels])
                arrays[name+'_input_'+str(level)] = source
                path = work/f'{name}_{level}.raw'
                payload = source
                if suffix == '_legacy':
                    payload = np.repeat(source[..., :1], 4, -1)
                    payload[..., 3] = source[..., 1]
                path.write_bytes(payload.tobytes())
                inputs.append(str(path))
            format_channels = 4 if suffix == '_legacy' else channels
            command = [executable, 'create', '--testrun', '--raw', '--width', '12', '--height', '8',
                       '--levels', str(count), '--format', ('R8', 'R8G8', 'R8G8B8', 'R8G8B8A8')[format_channels-1]+('_SRGB' if color else '_UNORM'),
                       '--assign-tf', 'srgb' if color else 'linear', '--assign-primaries', 'bt709' if color else 'none',
                       '--encode', 'basis-lz' if codec == 'etc1s' else 'uastc', '--threads', '1']
            if codec == 'etc1s':
                command += ['--qlevel', '255']
            if codec == 'uastc_zstd':
                command += ['--zstd', '5']
            output = root/(name+'.ktx2')
            subprocess.run(command+inputs+[str(output)], check=True, capture_output=True)
            if channels == 2 and codec != 'etc1s':
                # 4.4.2 encodes RG inputs as RGB with a zero blue channel.
                # Declare the payload's two used channels explicitly to cover
                # both the current RG and historical green-in-alpha RRRG DFDs.
                data = bytearray(output.read_bytes())
                dfd = struct.unpack_from('<I', data, 48)[0]
                data[dfd+31] = (data[dfd+31] & 240) | (5 if suffix == '_legacy' else 6)
                output.write_bytes(data)
            for level in range(count):
                raw = work/'extracted.raw'
                subprocess.run([executable, 'extract', '--transcode', 'rgba8', '--raw', '--level', str(level),
                                str(output), str(raw)], check=True, capture_output=True)
                arrays[name+'_decoded_'+str(level)] = np.frombuffer(raw.read_bytes(), np.uint8).reshape(
                    max(1, 8 >> level), max(1, 12 >> level), 4)
            validation = subprocess.run([executable, 'validate', '--gltf-basisu', '--format', 'mini-json', str(output)],
                                        text=True, capture_output=True)
            records.append(dict(name=name, codec=codec, channels=channels, srgb=color, levels=count,
                                sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                                validation=json.loads(validation.stdout)))
    np.savez_compressed(root/'pixels.npz', **arrays)
    (root/'manifest.json').write_text(json.dumps(dict(
        generator='Khronos KTX-Software 4.4.2; generate.py contains the original inputs and commands.',
        fixtures=records), indent=2)+'\n')
    print('Generated', len(records), 'KTX2 images and original/CLI-decoded mip controls')


if __name__ == '__main__':
    generate(sys.argv[1])
