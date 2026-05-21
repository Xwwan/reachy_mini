#!/usr/bin/env python3
import argparse
import gzip
import json
import subprocess
from pathlib import Path


PHASE_MARKERS = ('_1进入姿势.mp4', '_2可循环动作.mp4', '_3回正.mp4')


def parse_args():
    parser = argparse.ArgumentParser(
        description='Convert Reachy emoji mp4 clips into terminal frame assets.',
    )
    parser.add_argument('--source-root', type=Path, default=Path('videos'))
    parser.add_argument('--output-root', type=Path, default=Path('animations'))
    parser.add_argument('--width', type=int, default=64)
    parser.add_argument('--pixel-height', type=int, default=48)
    parser.add_argument('--fps', type=float, default=12.0)
    parser.add_argument('--threshold', type=int, default=48)
    return parser.parse_args()


def source_clips(root):
    for path in sorted(root.rglob('*.mp4')):
        if path.name.endswith(PHASE_MARKERS):
            yield path


def terminal_asset_path(source_root, output_root, path):
    relative = path.relative_to(source_root)
    return (output_root / relative).with_suffix('.tanim.json.gz')


def run_ffmpeg(path, width, pixel_height, fps):
    filter_chain = f'fps={fps},scale={width}:{pixel_height}:flags=area,format=gray'
    command = [
        'ffmpeg',
        '-v',
        'error',
        '-i',
        str(path),
        '-vf',
        filter_chain,
        '-an',
        '-sn',
        '-dn',
        '-f',
        'rawvideo',
        '-pix_fmt',
        'gray',
        '-',
    ]
    return subprocess.run(command, check=True, capture_output=True).stdout


def rows_to_cells(top, bottom, threshold):
    cells = []
    for upper, lower in zip(top, bottom):
        upper_on = upper >= threshold
        lower_on = lower >= threshold
        if upper_on and lower_on:
            cells.append('█')
        elif upper_on:
            cells.append('▀')
        elif lower_on:
            cells.append('▄')
        else:
            cells.append(' ')
    return ''.join(cells)


def raw_to_frames(raw, width, pixel_height, threshold):
    bytes_per_frame = width * pixel_height
    if len(raw) % bytes_per_frame:
        raise ValueError('ffmpeg returned a partial raw frame')

    frames = []
    for start in range(0, len(raw), bytes_per_frame):
        frame = raw[start:start + bytes_per_frame]
        lines = []
        for row_index in range(0, pixel_height, 2):
            top_start = row_index * width
            bottom_start = min(row_index + 1, pixel_height - 1) * width
            top = frame[top_start:top_start + width]
            bottom = frame[bottom_start:bottom_start + width]
            lines.append(rows_to_cells(top, bottom, threshold))
        frames.append('\n'.join(lines))
    return frames


def convert_clip(path, output_path, width, pixel_height, fps, threshold):
    raw = run_ffmpeg(path, width, pixel_height, fps)
    frames = raw_to_frames(raw, width, pixel_height, threshold)
    if not frames:
        raise ValueError(f'No frames decoded from {path}')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'version': 1,
        'source': str(path),
        'frame_seconds': 1.0 / fps,
        'frames': frames,
    }
    with gzip.open(output_path, 'wt', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(',', ':'))
    print(f'{path} -> {output_path} ({len(frames)} frames)')


def main():
    args = parse_args()
    if args.pixel_height < 2 or args.pixel_height % 2:
        raise SystemExit('--pixel-height must be an even integer >= 2')
    if not 0 <= args.threshold <= 255:
        raise SystemExit('--threshold must be between 0 and 255')
    if args.fps <= 0:
        raise SystemExit('--fps must be positive')

    clips = list(source_clips(args.source_root))
    if not clips:
        raise SystemExit(f'No entry/loop/exit clips found under {args.source_root}')

    for clip in clips:
        output_path = terminal_asset_path(args.source_root, args.output_root, clip)
        convert_clip(
            clip,
            output_path,
            args.width,
            args.pixel_height,
            args.fps,
            args.threshold,
        )


if __name__ == '__main__':
    main()
