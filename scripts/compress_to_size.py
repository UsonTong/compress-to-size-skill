#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TARGET_SIZE = '10MB'
DEFAULT_AUDIO_BITRATE = 96_000
MIN_VIDEO_BITRATE = 80_000
MIN_AUDIO_BITRATE = 32_000
SIZE_MARGIN = 0.96
TARGET_WINDOW_RATIO = 0.10
BITRATE_RAMP_UP = 1.25
SIZE_UNITS = {
    'KB': 1024,
    'MB': 1024 * 1024,
    'GB': 1024 * 1024 * 1024,
}


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    has_audio: bool
    width: int | None
    height: int | None


@dataclass(frozen=True)
class EncodePlan:
    video_bitrate: int
    audio_bitrate: int
    crf: int
    scale_width: int | None


def with_video_bitrate(plan: EncodePlan, video_bitrate: int) -> EncodePlan:
    return EncodePlan(
        video_bitrate=max(video_bitrate, MIN_VIDEO_BITRATE),
        audio_bitrate=plan.audio_bitrate,
        crf=plan.crf,
        scale_width=plan.scale_width,
    )


def parse_size(value: str) -> int:
    raw = value.strip().upper()
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([KMG]?B)?', raw)
    if not match:
        raise argparse.ArgumentTypeError('size must look like 10, 500KB, 10MB, or 2GB')

    number = float(match.group(1))
    unit = (match.group(2) or 'MB').upper()
    if unit == 'B':
        multiplier = 1
    else:
        normalized = unit if unit.endswith('B') else f'{unit}B'
        if normalized not in SIZE_UNITS:
            raise argparse.ArgumentTypeError('size unit must be KB, MB, or GB')
        multiplier = SIZE_UNITS[normalized]
    return max(1, int(number * multiplier))


def human_size(byte_count: int) -> str:
    for unit in ('GB', 'MB', 'KB'):
        size = SIZE_UNITS[unit]
        if byte_count >= size:
            return f'{byte_count / size:.2f}{unit}'
    return f'{byte_count}B'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Compress a local video to a target size using ffmpeg')
    parser.add_argument('input', nargs='?', help='Input video file')
    parser.add_argument('--input', dest='input_flag', help='Input video file, alternative to positional input')
    parser.add_argument('-o', '--output', help='Output file path')
    parser.add_argument('--output-dir', help='Directory for generated output when --output is not set')
    parser.add_argument('--target-size', type=parse_size, default=parse_size(DEFAULT_TARGET_SIZE), help='Target size such as 500KB, 10MB, 2GB, or bare 10 (defaults to MB)')
    parser.add_argument('--overwrite', action='store_true', help='Overwrite output path when it already exists')
    parser.add_argument('--dry-run', action='store_true', help='Print the ffmpeg plan without writing output')
    parser.add_argument('--max-attempts', type=int, default=8, help='Maximum compression attempts in total (default: 8)')
    parser.add_argument('--bisect-attempts', type=int, default=4, help='Maximum binary-search refinement attempts (default: 4)')
    parser.add_argument('--keep-audio', action='store_true', help='Keep audio when possible instead of dropping it at very low bitrates')
    args = parser.parse_args()

    if args.input and args.input_flag:
        parser.error('use either positional input or --input, not both')
    args.input_path = args.input_flag or args.input
    if not args.input_path:
        parser.error('input video path is required')
    if args.target_size <= 0:
        parser.error('--target-size must be greater than 0')
    if args.max_attempts < 1:
        parser.error('--max-attempts must be at least 1')
    if args.bisect_attempts < 0:
        parser.error('--bisect-attempts must be at least 0')
    if args.output and args.output_dir:
        parser.error('use either --output or --output-dir, not both')
    return args


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f'ERROR: {name} is required but was not found in PATH')


def resolve_input(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise SystemExit(f'ERROR: input file not found: {path}')
    return path


def unique_output_path(input_path: Path, output: str | None, output_dir: str | None, overwrite: bool) -> Path:
    if output:
        output_path = Path(output).expanduser().resolve()
    else:
        directory = Path(output_dir).expanduser().resolve() if output_dir else input_path.parent
        output_path = directory / f'{input_path.stem}.compressed.mp4'

    if input_path == output_path and not overwrite:
        raise SystemExit('ERROR: output path equals input path; pass --overwrite to allow replacement')
    if output_path.exists() and not overwrite:
        if output:
            raise SystemExit(f'ERROR: output already exists: {output_path}; pass --overwrite or choose another path')
        base = output_path.with_suffix('')
        suffix = output_path.suffix
        for index in range(2, 1000):
            candidate = Path(f'{base}.{index}{suffix}')
            if not candidate.exists():
                return candidate
        raise SystemExit('ERROR: could not find a free output filename')
    return output_path


def ffprobe(input_path: Path) -> MediaInfo:
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration:stream=codec_type,width,height',
        '-of', 'json',
        str(input_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f'ERROR: ffprobe failed: {proc.stderr.strip() or proc.stdout.strip()}')

    payload = json.loads(proc.stdout)
    duration = float(payload.get('format', {}).get('duration') or 0)
    streams = payload.get('streams') or []
    video_streams = [stream for stream in streams if stream.get('codec_type') == 'video']
    if not video_streams or duration <= 0:
        raise SystemExit('ERROR: input does not look like a readable video file')
    audio_streams = [stream for stream in streams if stream.get('codec_type') == 'audio']
    first_video = video_streams[0]
    return MediaInfo(
        duration=duration,
        has_audio=bool(audio_streams),
        width=int(first_video['width']) if first_video.get('width') else None,
        height=int(first_video['height']) if first_video.get('height') else None,
    )


def build_plan(info: MediaInfo, target_bytes: int, attempt: int, keep_audio: bool) -> EncodePlan:
    total_bitrate = max(int((target_bytes * 8 * SIZE_MARGIN) / info.duration), MIN_VIDEO_BITRATE)
    audio_bitrate = DEFAULT_AUDIO_BITRATE if info.has_audio else 0
    if total_bitrate < 450_000 and not keep_audio:
        audio_bitrate = 0
    elif info.has_audio:
        audio_bitrate = max(min(audio_bitrate, total_bitrate // 4), MIN_AUDIO_BITRATE)

    reduction = 0.82 ** attempt
    video_bitrate = max(int((total_bitrate - audio_bitrate) * reduction), MIN_VIDEO_BITRATE)
    crf = min(35, 28 + attempt * 2)

    scale_width = None
    if info.width and info.width > 1280:
        scale_width = 1280
    if attempt >= 2 and info.width and info.width > 854:
        scale_width = 854
    if attempt >= 3 and info.width and info.width > 640:
        scale_width = 640

    return EncodePlan(video_bitrate=video_bitrate, audio_bitrate=audio_bitrate, crf=crf, scale_width=scale_width)


def build_ffmpeg_cmd(input_path: Path, output_path: Path, plan: EncodePlan, overwrite: bool) -> list[str]:
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error']
    cmd.append('-y' if overwrite else '-n')
    cmd += ['-i', str(input_path), '-map', '0:v:0']

    filters = []
    if plan.scale_width:
        filters.append(f'scale={plan.scale_width}:-2')
    if filters:
        cmd += ['-vf', ','.join(filters)]

    cmd += [
        '-c:v', 'libx264',
        '-preset', 'medium',
        '-b:v', str(plan.video_bitrate),
        '-maxrate', str(plan.video_bitrate),
        '-bufsize', str(plan.video_bitrate * 2),
        '-crf', str(plan.crf),
        '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart',
    ]

    if plan.audio_bitrate > 0:
        cmd += ['-map', '0:a?', '-c:a', 'aac', '-b:a', str(plan.audio_bitrate), '-ac', '2']
    else:
        cmd += ['-an']

    cmd.append(str(output_path))
    return cmd


def run_encode(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f'exit code {proc.returncode}'
        raise SystemExit(f'ERROR: ffmpeg failed: {detail}')


def encode_once(input_path: Path, temp_path: Path, plan: EncodePlan) -> int:
    run_encode(build_ffmpeg_cmd(input_path, temp_path, plan, overwrite=True))
    size = temp_path.stat().st_size
    temp_path.unlink(missing_ok=True)
    return size


def encode_to_output(input_path: Path, output_path: Path, plan: EncodePlan) -> int:
    temp_path = output_path.with_name(f'.{output_path.stem}.tmp{output_path.suffix}')
    if temp_path.exists():
        temp_path.unlink()
    run_encode(build_ffmpeg_cmd(input_path, temp_path, plan, overwrite=True))
    if output_path.exists():
        output_path.unlink()
    temp_path.replace(output_path)
    return output_path.stat().st_size


def is_close_enough(size: int, target_bytes: int) -> bool:
    lower_bound = max(1, int(target_bytes * (1 - TARGET_WINDOW_RATIO)))
    return lower_bound <= size <= target_bytes


def search_best_plan(
    input_path: Path,
    temp_path: Path,
    base_plan: EncodePlan,
    target_bytes: int,
    max_attempts: int,
    bisect_attempts: int,
) -> tuple[EncodePlan, int, int]:
    attempts_used = 0
    best_plan = base_plan
    best_size = 0

    def remember(plan: EncodePlan, size: int) -> None:
        nonlocal best_plan, best_size
        if size <= target_bytes and size >= best_size:
            best_plan = plan
            best_size = size

    current_plan = base_plan
    current_size = encode_once(input_path, temp_path, current_plan)
    attempts_used += 1
    remember(current_plan, current_size)

    if is_close_enough(current_size, target_bytes):
        return current_plan, current_size, attempts_used

    if current_size > target_bytes:
        while attempts_used < max_attempts:
            next_bitrate = max(int(current_plan.video_bitrate * 0.82), MIN_VIDEO_BITRATE)
            if next_bitrate >= current_plan.video_bitrate:
                break
            current_plan = with_video_bitrate(base_plan, next_bitrate)
            current_size = encode_once(input_path, temp_path, current_plan)
            attempts_used += 1
            remember(current_plan, current_size)
            if is_close_enough(current_size, target_bytes):
                return current_plan, current_size, attempts_used
            if current_size <= target_bytes:
                break
        if current_size > target_bytes:
            return best_plan, current_size, attempts_used

    low_plan = current_plan
    low_size = current_size
    high_plan = with_video_bitrate(base_plan, max(int(low_plan.video_bitrate * BITRATE_RAMP_UP), low_plan.video_bitrate + 1))
    bracket_found = False

    while attempts_used < max_attempts:
        high_size = encode_once(input_path, temp_path, high_plan)
        attempts_used += 1
        if high_size > target_bytes:
            bracket_found = True
            break
        low_plan = high_plan
        low_size = high_size
        remember(low_plan, low_size)
        if is_close_enough(low_size, target_bytes):
            return low_plan, low_size, attempts_used
        next_bitrate = max(int(high_plan.video_bitrate * BITRATE_RAMP_UP), high_plan.video_bitrate + 1)
        if next_bitrate == high_plan.video_bitrate:
            break
        high_plan = with_video_bitrate(base_plan, next_bitrate)

    if not bracket_found:
        return best_plan, best_size or low_size, attempts_used

    lower_bitrate = low_plan.video_bitrate
    upper_bitrate = high_plan.video_bitrate
    for _ in range(min(bisect_attempts, max(0, max_attempts - attempts_used))):
        mid_bitrate = (lower_bitrate + upper_bitrate) // 2
        if mid_bitrate <= lower_bitrate:
            break
        mid_plan = with_video_bitrate(base_plan, mid_bitrate)
        mid_size = encode_once(input_path, temp_path, mid_plan)
        attempts_used += 1
        if mid_size <= target_bytes:
            low_plan = mid_plan
            low_size = mid_size
            remember(low_plan, low_size)
            if is_close_enough(low_size, target_bytes):
                return low_plan, low_size, attempts_used
            lower_bitrate = mid_bitrate
        else:
            upper_bitrate = mid_bitrate

    return best_plan, best_size or low_size, attempts_used


def main() -> int:
    args = parse_args()
    require_tool('ffmpeg')
    require_tool('ffprobe')

    input_path = resolve_input(args.input_path)
    target_bytes = args.target_size
    output_path = unique_output_path(input_path, args.output, args.output_dir, args.overwrite)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    info = ffprobe(input_path)
    original_size = input_path.stat().st_size

    if original_size <= target_bytes and not args.overwrite:
        print('Already under target')
        print(f'Input: {input_path}')
        print(f'Size: {human_size(original_size)}')
        return 0

    initial_plan = build_plan(info, target_bytes, 0, args.keep_audio)

    if args.dry_run:
        print('Dry run:')
        print(' '.join(build_ffmpeg_cmd(input_path, output_path, initial_plan, overwrite=False)))
        print(f'Target: {human_size(target_bytes)}')
        print(f'Window: {human_size(max(1, int(target_bytes * (1 - TARGET_WINDOW_RATIO))))} - {human_size(target_bytes)}')
        print(f'Max attempts: {args.max_attempts}')
        print(f'Bisect attempts: {args.bisect_attempts}')
        print(f'Estimated video bitrate: {initial_plan.video_bitrate // 1000}k')
        if initial_plan.audio_bitrate:
            print(f'Estimated audio bitrate: {initial_plan.audio_bitrate // 1000}k')
        else:
            print('Audio: disabled')
        return 0

    probe_path = output_path.with_name(f'.{output_path.stem}.probe{output_path.suffix}')
    if probe_path.exists():
        probe_path.unlink()

    best_plan, best_size, attempts_used = search_best_plan(
        input_path=input_path,
        temp_path=probe_path,
        base_plan=initial_plan,
        target_bytes=target_bytes,
        max_attempts=args.max_attempts,
        bisect_attempts=args.bisect_attempts,
    )

    if probe_path.exists():
        probe_path.unlink()

    if best_size > target_bytes or best_size == 0:
        print('Compression finished but output is still above target', file=sys.stderr)
        print(f'Input: {input_path}', file=sys.stderr)
        print(f'Target: {human_size(target_bytes)}', file=sys.stderr)
        print(f'Last size: {human_size(best_size)}', file=sys.stderr)
        print(f'Attempts: {attempts_used}', file=sys.stderr)
        return 2

    final_size = encode_to_output(input_path, output_path, best_plan)
    ratio = (1 - final_size / original_size) * 100 if original_size else 0
    print('Compression complete')
    print(f'Input: {input_path}')
    print(f'Output: {output_path}')
    print(f'Original size: {human_size(original_size)}')
    print(f'Compressed size: {human_size(final_size)}')
    print(f'Reduction: {ratio:.1f}%')
    print(f'Window met: {"yes" if is_close_enough(final_size, target_bytes) else "no"}')
    print(f'Attempts: {attempts_used}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
