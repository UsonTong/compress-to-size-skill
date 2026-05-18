---
name: compress-to-size
description: Compress local video files to a target size using ffmpeg. Supports KB, MB, and GB; bare numbers default to MB.
---

# Compress to Size

Compress a local video file to a target size.

## Usage

```bash
scripts/compress-to-size /path/to/video.mp4
```

```bash
scripts/compress-to-size --target-size 500KB /path/to/video.mp4
```

```bash
scripts/compress-to-size --target-size 20MB --output /tmp/video.small.mp4 /path/to/video.mp4
```

```bash
scripts/compress-to-size --target-size 2GB --dry-run /path/to/video.mp4
```

## Options

- `--target-size 10MB`: target size; supports `KB`, `MB`, `GB`; bare numbers default to MB.
- `--bisect-attempts 4`: maximum binary-search refinement attempts.
- `--output /path/to/output.mp4`: custom output path.
- `--output-dir /path/to/dir`: generated output directory.
- `--overwrite`: overwrite an existing output path.
- `--dry-run`: print the encode plan without writing output.

## Notes

- Requires `ffmpeg` and `ffprobe`.
- Defaults to `10MB` when `--target-size` is not set.
- Defaults to writing a new file beside the source video.
- Tries to keep the result within 10% below the target size when possible.
- Never overwrites the source file unless explicitly allowed with `--overwrite`.
