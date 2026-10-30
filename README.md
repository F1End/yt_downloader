# YouTube Chapter Slicer

Download a YouTube video's audio as MP3 and split it into one file per
chapter when chapter markers are present. Videos without chapters are saved
as a single MP3.

## Requirements

- Python 3.11+
- `yt-dlp`
- `ffmpeg` (system install, or the bundled binary from `imageio-ffmpeg`)

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Usage

```bash
.venv/bin/python yt_chapter_slicer.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

Quote URLs containing `&` or other shell metacharacters.

Options:

```text
-o, --output-dir DIR   Output directory (default: ./output)
--bitrate RATE         MP3 bitrate passed to ffmpeg (default: 192K)
--ffmpeg PATH          Explicit ffmpeg binary
--single               Save one full MP3 even if chapters exist
--dry-run              Show detected chapters and planned filenames
--keep-full            Keep the downloaded source when splitting
--no-overwrites        Skip output files that already exist
```

Examples:

```bash
.venv/bin/python yt_chapter_slicer.py --dry-run "https://www.youtube.com/watch?v=VIDEO_ID"
.venv/bin/python yt_chapter_slicer.py --single -o downloads "https://www.youtube.com/watch?v=VIDEO_ID"
```

## Output

Chaptered videos are written to `OUTPUT_DIR/<video title>/` as numbered MP3
files. Chapterless videos are written to `OUTPUT_DIR/<video title>.mp3`.

## Testing

```bash
.venv/bin/python -m pytest -q
```

## Notes

Only download content you own or are authorized to download.
