# compress-to-size-skill

一个用于把本地视频压缩到指定大小以内的 skill。

## 功能说明

- 输入一个本地视频文件
- 使用 `ffprobe` 读取视频时长和媒体信息
- 使用 `ffmpeg` 自动计算码率并压缩为 MP4
- 默认输出新文件，不覆盖源文件
- 支持 `--target-size` 指定目标大小
- 支持 KB、MB、GB，纯数字默认按 MB 处理
- 默认会在 10% 以内尽量贴近目标大小
- 支持 `--bisect-attempts` 控制二分细化次数
- 支持 `--dry-run` 预览压缩参数

## 前置要求

系统需要安装：

```bash
ffmpeg
ffprobe
python3
```

## 常用命令

压缩到默认大小 10MB：

```bash
scripts/compress-to-size /absolute/path/to/video.mp4
```

压缩到 500KB：

```bash
scripts/compress-to-size --target-size 500KB /absolute/path/to/video.mp4
```

压缩到 2GB：

```bash
scripts/compress-to-size --target-size 2GB /absolute/path/to/video.mp4
```

纯数字默认按 MB 处理：

```bash
scripts/compress-to-size --target-size 20 /absolute/path/to/video.mp4
```

指定输出目录：

```bash
python3 scripts/compress_to_size.py \
  --input /absolute/path/to/video.mp4 \
  --target-size 10MB \
  --output-dir outputs/
```

控制二分细化次数：

```bash
scripts/compress-to-size --target-size 10MB --bisect-attempts 6 /absolute/path/to/video.mp4
```

预览参数，不实际压缩：

```bash
scripts/compress-to-size --target-size 10MB --dry-run /absolute/path/to/video.mp4
```

## 输出

成功后会打印：

- 输入路径
- 输出路径
- 原始大小
- 压缩后大小
- 是否在 10% 窗口内

## 验证

```bash
python3 scripts/compress_to_size.py --help
python3 -m py_compile scripts/*.py
bash scripts/smoke_test.sh
```
