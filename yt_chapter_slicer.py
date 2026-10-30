#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TypedDict


class Chapter(TypedDict):
    index: int
    title: str
    start: float
    end: float | None


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def resolve_ffmpeg(explicit: str | None = None) -> str:
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        raise RuntimeError(f"Not an executable ffmpeg binary: {candidate}")

    env_path = os.environ.get("FFMPEG_BINARY")
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        raise RuntimeError(f"FFMPEG_BINARY is not an executable file: {candidate}")

    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError(
            "ffmpeg is not on PATH and imageio-ffmpeg is not installed in this "
            f"Python interpreter ({sys.executable}).\n"
            f"Install it with: {sys.executable} -m pip install imageio-ffmpeg\n"
            "Or install system ffmpeg: sudo apt-get install ffmpeg"
        ) from exc

    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError(
            "imageio-ffmpeg is installed, but its bundled ffmpeg binary could not "
            "be used.\n"
            f"Reinstall it with: {sys.executable} -m pip install --force-reinstall imageio-ffmpeg\n"
            "Or install system ffmpeg: sudo apt-get install ffmpeg"
        ) from exc


def sanitize(name: object, max_len: int = 150) -> str:
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", str(name))
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    name = name[:max_len].rstrip(". ")
    return name or "audio"


def fetch_info(url: str, ffmpeg: str) -> dict[str, Any]:
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--dump-single-json",
        "--no-playlist",
        "--skip-download",
        "--no-warnings",
        "--ffmpeg-location",
        ffmpeg,
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "yt-dlp metadata extraction failed")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse yt-dlp metadata: {exc}") from exc


def build_chapters(info: Mapping[str, Any]) -> list[Chapter]:
    raw = info.get("chapters") or []
    duration = info.get("duration")
    chapters: list[Chapter] = []
    for position, chapter in enumerate(raw):
        start = float(chapter.get("start_time") or 0)
        end = chapter.get("end_time")
        if end is None:
            if position + 1 < len(raw):
                end = float(raw[position + 1].get("start_time") or 0)
            elif duration is not None:
                end = float(duration)
            else:
                end = None
        if end is not None:
            end = float(end)
        title = str(chapter.get("title") or f"Chapter {position + 1}").strip()
        if end is not None and end <= start:
            eprint(f"Skipping empty chapter: {title}")
            continue
        chapters.append(
            Chapter(
                index=len(chapters) + 1,
                title=title,
                start=start,
                end=end,
            )
        )
    return chapters


def unique_names(chapters: Iterable[Chapter]) -> list[str]:
    used: set[str] = set()
    names: list[str] = []
    for chapter in chapters:
        title = sanitize(chapter["title"], max_len=110)
        base = sanitize(f"{chapter['index']:02d} - {title}", max_len=120)
        candidate = base
        counter = 2
        while candidate.lower() in used:
            candidate = f"{base} ({counter})"
            counter += 1
        used.add(candidate.lower())
        names.append(f"{candidate}.mp3")
    return names


def format_time(seconds: float | None) -> str:
    if seconds is None:
        return "??:??:??"
    total = max(0, int(round(seconds)))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def download_source(url: str, ffmpeg: str, outdir: Path) -> tuple[Path, Path]:
    tmpdir = Path(tempfile.mkdtemp(prefix=".download-", dir=outdir))
    template = str(tmpdir / "%(title)s.%(ext)s")
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--no-warnings",
        "--newline",
        "--no-overwrites",
        "-f",
        "bestaudio/best",
        "-o",
        template,
        "--ffmpeg-location",
        ffmpeg,
        url,
    ]
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise RuntimeError("yt-dlp download failed")
    files = [
        path
        for path in tmpdir.rglob("*")
        if path.is_file() and not path.name.endswith((".part", ".ytdl"))
    ]
    if not files:
        raise RuntimeError("yt-dlp finished but no audio file was found")
    source = max(files, key=lambda path: path.stat().st_size)
    return source, tmpdir


def convert_to_mp3(
    ffmpeg: str,
    source: Path,
    destination: Path,
    bitrate: str,
    title: str | None = None,
    track: str | None = None,
    start: float | None = None,
    duration: float | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-i", str(source), "-vn", "-c:a", "libmp3lame", "-b:a", bitrate]
    if title:
        cmd += ["-metadata", f"title={title}"]
    if track:
        cmd += ["-metadata", f"track={track}"]
    cmd += [str(destination)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffmpeg conversion failed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download YouTube audio as MP3, splitting at chapter markers when present."
    )
    parser.add_argument("url", nargs="?", help="YouTube video URL (prompted when omitted)")
    parser.add_argument(
        "-o",
        "--output-dir",
        default="output",
        help="Directory for finished MP3 files (default: ./output)",
    )
    parser.add_argument(
        "--bitrate",
        default="192K",
        help="MP3 bitrate passed to ffmpeg (default: 192K)",
    )
    parser.add_argument(
        "--ffmpeg",
        help="Path to a specific ffmpeg binary (overrides PATH and imageio-ffmpeg)",
    )
    parser.add_argument(
        "--single",
        action="store_true",
        help="Save one full MP3 even when the video has chapters",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show detected chapters and planned filenames without downloading",
    )
    parser.add_argument(
        "--keep-full",
        action="store_true",
        help="Keep the downloaded source file when splitting chapters",
    )
    parser.add_argument(
        "--no-overwrites",
        action="store_true",
        help="Skip output files that already exist",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    url = (args.url or input("YouTube URL: ")).strip()
    if not url:
        eprint("No URL provided")
        return 2

    try:
        ffmpeg = resolve_ffmpeg(args.ffmpeg)
    except RuntimeError as exc:
        eprint(f"ffmpeg error: {exc}")
        return 1

    outdir = Path(args.output_dir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    try:
        info = fetch_info(url, ffmpeg)
    except RuntimeError as exc:
        eprint(f"Metadata error: {exc}")
        return 1

    title = str(info.get("title") or "audio").strip()
    chapters = build_chapters(info)
    use_chapters = bool(chapters) and not args.single
    single_path = outdir / f"{sanitize(title)}.mp3"

    chapter_names: list[str] = unique_names(chapters) if use_chapters else []
    chapter_dir = outdir / sanitize(title)
    chapter_paths: list[Path] = [chapter_dir / name for name in chapter_names]

    if args.dry_run:
        print(f"Title:  {title}")
        print(f"Output: {outdir}")
        if use_chapters:
            print(f"Mode:   chapters ({len(chapters)})")
            for chapter, path in zip(chapters, chapter_paths):
                end = format_time(chapter["end"])
                start = format_time(chapter["start"])
                print(
                    f"  {chapter['index']:02d}  {start} - {end}  "
                    f"{chapter['title']}  ->  {path.relative_to(outdir)}"
                )
        elif chapters and args.single:
            print(f"Mode:   single file (forced; {len(chapters)} chapters ignored)")
            print(f"File:   {single_path.relative_to(outdir)}")
        else:
            print("Mode:   single file (no chapters found)")
            print(f"File:   {single_path.relative_to(outdir)}")
        return 0

    pending: list[tuple[Chapter, Path]] = []
    if use_chapters:
        pending = [
            (chapter, path)
            for chapter, path in zip(chapters, chapter_paths)
            if not (args.no_overwrites and path.exists())
        ]
        if not pending and not args.keep_full:
            print("All chapter files already exist; nothing to do.")
            return 0
    else:
        if args.no_overwrites and single_path.exists():
            print(f"Already exists: {single_path}")
            return 0

    tmpdir: Path | None = None
    source: Path | None = None
    keep_tmp: bool = False
    try:
        print(f"Downloading audio: {title}")
        source, tmpdir = download_source(url, ffmpeg, outdir)
        print(f"Downloaded source: {source.name}")

        if use_chapters:
            for chapter, path in zip(chapters, chapter_paths):
                if args.no_overwrites and path.exists():
                    continue
                duration = None if chapter["end"] is None else chapter["end"] - chapter["start"]
                convert_to_mp3(
                    ffmpeg,
                    source,
                    path,
                    args.bitrate,
                    title=chapter["title"],
                    track=f"{chapter['index']}/{len(chapters)}",
                    start=chapter["start"],
                    duration=duration,
                )
                print(f"  wrote {path.relative_to(outdir)}")

            if args.keep_full:
                kept = outdir / f"{sanitize(title)} (full){source.suffix}"
                if args.no_overwrites and kept.exists():
                    print(f"  kept existing {kept.relative_to(outdir)}")
                else:
                    shutil.copy2(source, kept)
                    print(f"  kept {kept.relative_to(outdir)}")
        else:
            convert_to_mp3(
                ffmpeg,
                source,
                single_path,
                args.bitrate,
                title=title,
            )
            print(f"Wrote {single_path}")

        print("Done.")
        return 0
    except (RuntimeError, OSError) as exc:
        eprint(f"Error: {exc}")
        if source is not None and source.exists():
            keep_tmp = True
            eprint(f"Downloaded source retained in: {tmpdir}")
        return 1
    finally:
        if tmpdir is not None and tmpdir.exists() and not keep_tmp:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
