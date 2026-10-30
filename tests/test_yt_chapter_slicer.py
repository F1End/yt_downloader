from __future__ import annotations

import argparse
import subprocess
import sys
import types
from pathlib import Path

import pytest

import yt_chapter_slicer as slicer

URL = "https://www.youtube.com/watch?v=test"


def make_args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "url": URL,
        "output_dir": str(tmp_path / "out"),
        "bitrate": "192K",
        "ffmpeg": "/fake/ffmpeg",
        "single": False,
        "dry_run": False,
        "keep_full": False,
        "no_overwrites": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def make_executable(tmp_path: Path) -> Path:
    path = tmp_path / "ffmpeg"
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def chapter(
    index: int,
    title: str,
    start: float,
    end: float | None,
) -> slicer.Chapter:
    return slicer.Chapter(index=index, title=title, start=start, end=end)


def test_sanitize_replaces_illegal_characters() -> None:
    assert slicer.sanitize('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"


def test_sanitize_collapses_whitespace_and_control_characters() -> None:
    assert slicer.sanitize("  a\t b\nc  ") == "a_ b_c"


def test_sanitize_strips_surrounding_dots() -> None:
    assert slicer.sanitize("...name...") == "name"


def test_sanitize_returns_fallback_for_empty_name() -> None:
    assert slicer.sanitize("   ") == "audio"
    assert slicer.sanitize("...") == "audio"


def test_sanitize_truncates_to_max_length() -> None:
    assert slicer.sanitize("a" * 200, max_len=10) == "a" * 10


def test_sanitize_removes_trailing_dot_after_truncation() -> None:
    assert slicer.sanitize("abcdef.", max_len=7) == "abcdef"


def test_format_time_handles_values() -> None:
    assert slicer.format_time(None) == "??:??:??"
    assert slicer.format_time(0) == "00:00:00"
    assert slicer.format_time(59) == "00:00:59"
    assert slicer.format_time(59.6) == "00:01:00"
    assert slicer.format_time(3661) == "01:01:01"
    assert slicer.format_time(-5) == "00:00:00"


def test_build_chapters_returns_empty_without_chapters() -> None:
    assert slicer.build_chapters({"duration": 100}) == []


def test_build_chapters_uses_next_start_for_missing_end() -> None:
    info = {
        "duration": 100,
        "chapters": [
            {"start_time": 0, "title": "Intro"},
            {"start_time": 30, "title": "Main"},
        ],
    }

    assert slicer.build_chapters(info) == [
        chapter(1, "Intro", 0.0, 30.0),
        chapter(2, "Main", 30.0, 100.0),
    ]


def test_build_chapters_uses_explicit_end_and_duration() -> None:
    info = {
        "duration": 90,
        "chapters": [
            {"start_time": 0, "end_time": 25, "title": "Intro"},
            {"start_time": 25, "title": "Main"},
        ],
    }

    assert slicer.build_chapters(info) == [
        chapter(1, "Intro", 0.0, 25.0),
        chapter(2, "Main", 25.0, 90.0),
    ]


def test_build_chapters_leaves_last_end_unknown_without_duration() -> None:
    info = {"chapters": [{"start_time": 10, "title": "Only"}]}

    assert slicer.build_chapters(info) == [chapter(1, "Only", 10.0, None)]


def test_build_chapters_skips_empty_chapters_and_renumbers() -> None:
    info = {
        "duration": 60,
        "chapters": [
            {"start_time": 0, "end_time": 10, "title": "Empty"},
            {"start_time": 10, "end_time": 10, "title": "Also empty"},
            {"start_time": 20, "title": "Kept"},
        ],
    }

    assert slicer.build_chapters(info) == [
        chapter(1, "Empty", 0.0, 10.0),
        chapter(2, "Kept", 20.0, 60.0),
    ]


def test_build_chapters_uses_default_title_and_zero_start() -> None:
    info = {"duration": 10, "chapters": [{"end_time": 10}]}

    assert slicer.build_chapters(info) == [chapter(1, "Chapter 1", 0.0, 10.0)]


def test_unique_names_includes_index_and_extension() -> None:
    chapters = [
        chapter(1, "Intro", 0.0, 10.0),
        chapter(2, "Main", 10.0, 20.0),
    ]

    assert slicer.unique_names(chapters) == ["01 - Intro.mp3", "02 - Main.mp3"]


def test_unique_names_sanitizes_titles() -> None:
    chapters = [chapter(1, "A/B:C", 0.0, 10.0)]

    assert slicer.unique_names(chapters) == ["01 - A_B_C.mp3"]


def test_unique_names_deduplicates_collisions() -> None:
    chapters = [
        chapter(1, "Same", 0.0, 10.0),
        chapter(1, "Same", 10.0, 20.0),
    ]

    assert slicer.unique_names(chapters) == ["01 - Same.mp3", "01 - Same (2).mp3"]


def test_unique_names_uses_fallback_for_empty_title() -> None:
    chapters = [chapter(1, "", 0.0, 10.0)]

    assert slicer.unique_names(chapters) == ["01 - audio.mp3"]


def test_resolve_ffmpeg_uses_explicit_path(tmp_path: Path) -> None:
    executable = make_executable(tmp_path)

    assert slicer.resolve_ffmpeg(str(executable)) == str(executable)


def test_resolve_ffmpeg_rejects_missing_explicit_path(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Not an executable ffmpeg binary"):
        slicer.resolve_ffmpeg(str(tmp_path / "missing"))


def test_resolve_ffmpeg_rejects_non_executable_explicit_path(tmp_path: Path) -> None:
    path = tmp_path / "ffmpeg"
    path.write_text("not executable")

    with pytest.raises(RuntimeError, match="Not an executable ffmpeg binary"):
        slicer.resolve_ffmpeg(str(path))


def test_resolve_ffmpeg_uses_environment_variable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = make_executable(tmp_path)
    monkeypatch.setenv("FFMPEG_BINARY", str(executable))

    assert slicer.resolve_ffmpeg() == str(executable)


def test_resolve_ffmpeg_rejects_invalid_environment_variable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FFMPEG_BINARY", str(tmp_path / "missing"))

    with pytest.raises(RuntimeError, match="FFMPEG_BINARY is not an executable file"):
        slicer.resolve_ffmpeg()


def test_resolve_ffmpeg_uses_path_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FFMPEG_BINARY", raising=False)
    monkeypatch.setattr(slicer.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    assert slicer.resolve_ffmpeg() == "/usr/bin/ffmpeg"


def test_resolve_ffmpeg_falls_back_to_imageio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = types.ModuleType("imageio_ffmpeg")
    fake_module.get_ffmpeg_exe = lambda: "/fake/ffmpeg"
    monkeypatch.delenv("FFMPEG_BINARY", raising=False)
    monkeypatch.setattr(slicer.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake_module)

    assert slicer.resolve_ffmpeg() == "/fake/ffmpeg"


def test_resolve_ffmpeg_reports_missing_imageio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FFMPEG_BINARY", raising=False)
    monkeypatch.setattr(slicer.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)

    with pytest.raises(RuntimeError, match="imageio-ffmpeg is not installed"):
        slicer.resolve_ffmpeg()


def test_resolve_ffmpeg_reports_broken_imageio_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = types.ModuleType("imageio_ffmpeg")

    def fail() -> str:
        raise RuntimeError("broken")

    fake_module.get_ffmpeg_exe = fail
    monkeypatch.delenv("FFMPEG_BINARY", raising=False)
    monkeypatch.setattr(slicer.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake_module)

    with pytest.raises(RuntimeError, match="could not be used"):
        slicer.resolve_ffmpeg()


def test_fetch_info_parses_json_and_builds_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        assert kwargs == {"capture_output": True, "text": True}
        return subprocess.CompletedProcess(cmd, 0, stdout='{"title": "Video"}', stderr="")

    monkeypatch.setattr(slicer.subprocess, "run", fake_run)

    assert slicer.fetch_info(URL, "/fake/ffmpeg") == {"title": "Video"}
    assert "--dump-single-json" in captured["cmd"]
    assert URL in captured["cmd"]
    assert "/fake/ffmpeg" in captured["cmd"]


def test_fetch_info_reports_subprocess_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="metadata boom")

    monkeypatch.setattr(slicer.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="metadata boom"):
        slicer.fetch_info(URL, "/fake/ffmpeg")


def test_fetch_info_uses_default_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(slicer.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="metadata extraction failed"):
        slicer.fetch_info(URL, "/fake/ffmpeg")


def test_fetch_info_reports_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr="")

    monkeypatch.setattr(slicer.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="Could not parse yt-dlp metadata"):
        slicer.fetch_info(URL, "/fake/ffmpeg")


def test_download_source_returns_largest_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        template = Path(cmd[cmd.index("-o") + 1])
        workdir = template.parent
        (workdir / "small.webm").write_bytes(b"x")
        (workdir / "large.webm").write_bytes(b"x" * 100)
        (workdir / "large.webm.part").write_bytes(b"x" * 1000)
        (workdir / "info.ytdl").write_text("x")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(slicer.subprocess, "run", fake_run)

    source, tmpdir = slicer.download_source(URL, "/fake/ffmpeg", tmp_path)

    assert source.name == "large.webm"
    assert tmpdir.parent == tmp_path
    assert "--no-playlist" in captured["cmd"]
    assert "bestaudio/best" in captured["cmd"]


def test_download_source_reports_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(slicer.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="download failed"):
        slicer.download_source(URL, "/fake/ffmpeg", tmp_path)


def test_download_source_reports_missing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(slicer.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="no audio file was found"):
        slicer.download_source(URL, "/fake/ffmpeg", tmp_path)


def test_convert_to_mp3_builds_command_with_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(slicer.subprocess, "run", fake_run)

    source = tmp_path / "source.webm"
    source.write_bytes(b"audio")
    destination = tmp_path / "nested" / "out.mp3"

    slicer.convert_to_mp3(
        "/ffmpeg",
        source,
        destination,
        "128K",
        title="Intro",
        track="1/2",
        start=1.5,
        duration=2.5,
    )

    cmd = captured["cmd"]
    assert destination.parent.is_dir()
    assert cmd[cmd.index("-ss") + 1] == "1.500"
    assert cmd[cmd.index("-t") + 1] == "2.500"
    assert cmd[cmd.index("-b:a") + 1] == "128K"
    assert "title=Intro" in cmd
    assert "track=1/2" in cmd
    assert cmd[-1] == str(destination)


def test_convert_to_mp3_omits_optional_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(slicer.subprocess, "run", fake_run)

    source = tmp_path / "source.webm"
    source.write_bytes(b"audio")
    destination = tmp_path / "out.mp3"

    slicer.convert_to_mp3("/ffmpeg", source, destination, "192K")

    cmd = captured["cmd"]
    assert "-ss" not in cmd
    assert "-t" not in cmd
    assert "-metadata" not in cmd
    assert cmd[-1] == str(destination)


def test_convert_to_mp3_reports_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ffmpeg boom")

    monkeypatch.setattr(slicer.subprocess, "run", fake_run)

    source = tmp_path / "source.webm"
    source.write_bytes(b"audio")

    with pytest.raises(RuntimeError, match="ffmpeg boom"):
        slicer.convert_to_mp3("/ffmpeg", source, tmp_path / "out.mp3", "192K")


def test_parse_args_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["prog", URL])

    args = slicer.parse_args()

    assert args.url == URL
    assert args.output_dir == "output"
    assert args.bitrate == "192K"
    assert args.ffmpeg is None
    assert args.single is False
    assert args.dry_run is False
    assert args.keep_full is False
    assert args.no_overwrites is False


def test_parse_args_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            URL,
            "-o",
            "downloads",
            "--bitrate",
            "128K",
            "--ffmpeg",
            "/opt/ffmpeg",
            "--single",
            "--dry-run",
            "--keep-full",
            "--no-overwrites",
        ],
    )

    args = slicer.parse_args()

    assert args.output_dir == "downloads"
    assert args.bitrate == "128K"
    assert args.ffmpeg == "/opt/ffmpeg"
    assert args.single is True
    assert args.dry_run is True
    assert args.keep_full is True
    assert args.no_overwrites is True


def test_main_dry_run_reports_chapters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = make_args(tmp_path, dry_run=True)
    info = {
        "title": "My Video",
        "duration": 100,
        "chapters": [
            {"start_time": 0, "end_time": 30, "title": "Intro"},
            {"start_time": 30, "end_time": 100, "title": "Main"},
        ],
    }
    monkeypatch.setattr(slicer, "parse_args", lambda: args)
    monkeypatch.setattr(slicer, "resolve_ffmpeg", lambda explicit=None: "/fake/ffmpeg")
    monkeypatch.setattr(slicer, "fetch_info", lambda url, ffmpeg: info)

    assert slicer.main() == 0

    output = capsys.readouterr().out
    assert "Mode:   chapters (2)" in output
    assert "01  00:00:00 - 00:00:30  Intro" in output
    assert "02  00:00:30 - 00:01:40  Main" in output


def test_main_dry_run_reports_single_file_when_no_chapters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = make_args(tmp_path, dry_run=True)
    monkeypatch.setattr(slicer, "parse_args", lambda: args)
    monkeypatch.setattr(slicer, "resolve_ffmpeg", lambda explicit=None: "/fake/ffmpeg")
    monkeypatch.setattr(
        slicer,
        "fetch_info",
        lambda url, ffmpeg: {"title": "My Video", "duration": 100},
    )

    assert slicer.main() == 0

    output = capsys.readouterr().out
    assert "Mode:   single file (no chapters found)" in output
    assert "My Video.mp3" in output


def test_main_dry_run_honors_single_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = make_args(tmp_path, dry_run=True, single=True)
    info = {
        "title": "My Video",
        "duration": 100,
        "chapters": [{"start_time": 0, "end_time": 100, "title": "Only"}],
    }
    monkeypatch.setattr(slicer, "parse_args", lambda: args)
    monkeypatch.setattr(slicer, "resolve_ffmpeg", lambda explicit=None: "/fake/ffmpeg")
    monkeypatch.setattr(slicer, "fetch_info", lambda url, ffmpeg: info)

    assert slicer.main() == 0

    output = capsys.readouterr().out
    assert "single file (forced; 1 chapters ignored)" in output


def test_main_downloads_and_converts_single_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = make_args(tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()
    source = workdir / "source.webm"
    source.write_bytes(b"audio")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_convert(*call_args: object, **call_kwargs: object) -> None:
        calls.append((call_args, call_kwargs))
        Path(call_args[2]).write_bytes(b"mp3")

    monkeypatch.setattr(slicer, "parse_args", lambda: args)
    monkeypatch.setattr(slicer, "resolve_ffmpeg", lambda explicit=None: "/fake/ffmpeg")
    monkeypatch.setattr(
        slicer,
        "fetch_info",
        lambda url, ffmpeg: {"title": "My Video", "duration": 100},
    )
    monkeypatch.setattr(
        slicer,
        "download_source",
        lambda url, ffmpeg, outdir: (source, workdir),
    )
    monkeypatch.setattr(slicer, "convert_to_mp3", fake_convert)

    assert slicer.main() == 0

    assert calls == [
        (
            (
                "/fake/ffmpeg",
                source,
                tmp_path / "out" / "My Video.mp3",
                "192K",
            ),
            {"title": "My Video"},
        )
    ]
    assert not workdir.exists()


def test_main_splits_chapters_and_keeps_full_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = make_args(tmp_path, keep_full=True)
    workdir = tmp_path / "work"
    workdir.mkdir()
    source = workdir / "source.webm"
    source.write_bytes(b"audio")
    info = {
        "title": "My Video",
        "duration": 100,
        "chapters": [
            {"start_time": 0, "end_time": 30, "title": "Intro"},
            {"start_time": 30, "end_time": 100, "title": "Main"},
        ],
    }
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    copied: list[tuple[Path, Path]] = []

    def fake_convert(*call_args: object, **call_kwargs: object) -> None:
        calls.append((call_args, call_kwargs))
        destination = Path(call_args[2])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"mp3")

    def fake_copy2(src: Path, dst: Path) -> None:
        copied.append((src, dst))

    monkeypatch.setattr(slicer, "parse_args", lambda: args)
    monkeypatch.setattr(slicer, "resolve_ffmpeg", lambda explicit=None: "/fake/ffmpeg")
    monkeypatch.setattr(slicer, "fetch_info", lambda url, ffmpeg: info)
    monkeypatch.setattr(
        slicer,
        "download_source",
        lambda url, ffmpeg, outdir: (source, workdir),
    )
    monkeypatch.setattr(slicer, "convert_to_mp3", fake_convert)
    monkeypatch.setattr(slicer.shutil, "copy2", fake_copy2)

    assert slicer.main() == 0

    assert calls == [
        (
            (
                "/fake/ffmpeg",
                source,
                tmp_path / "out" / "My Video" / "01 - Intro.mp3",
                "192K",
            ),
            {
                "title": "Intro",
                "track": "1/2",
                "start": 0.0,
                "duration": 30.0,
            },
        ),
        (
            (
                "/fake/ffmpeg",
                source,
                tmp_path / "out" / "My Video" / "02 - Main.mp3",
                "192K",
            ),
            {
                "title": "Main",
                "track": "2/2",
                "start": 30.0,
                "duration": 70.0,
            },
        ),
    ]
    assert copied == [
        (source, tmp_path / "out" / "My Video (full).webm"),
    ]
    assert not workdir.exists()


def test_main_skips_existing_single_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = make_args(tmp_path, no_overwrites=True)
    outdir = tmp_path / "out"
    outdir.mkdir()
    existing = outdir / "My Video.mp3"
    existing.write_bytes(b"existing")

    monkeypatch.setattr(slicer, "parse_args", lambda: args)
    monkeypatch.setattr(slicer, "resolve_ffmpeg", lambda explicit=None: "/fake/ffmpeg")
    monkeypatch.setattr(
        slicer,
        "fetch_info",
        lambda url, ffmpeg: {"title": "My Video", "duration": 100},
    )

    assert slicer.main() == 0

    assert "Already exists" in capsys.readouterr().out
    assert existing.read_bytes() == b"existing"


def test_main_skips_existing_chapter_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = make_args(tmp_path, no_overwrites=True)
    outdir = tmp_path / "out"
    chapter_dir = outdir / "My Video"
    chapter_dir.mkdir(parents=True)
    for name in ("01 - Intro.mp3", "02 - Main.mp3"):
        (chapter_dir / name).write_bytes(b"existing")
    info = {
        "title": "My Video",
        "duration": 100,
        "chapters": [
            {"start_time": 0, "end_time": 30, "title": "Intro"},
            {"start_time": 30, "end_time": 100, "title": "Main"},
        ],
    }

    monkeypatch.setattr(slicer, "parse_args", lambda: args)
    monkeypatch.setattr(slicer, "resolve_ffmpeg", lambda explicit=None: "/fake/ffmpeg")
    monkeypatch.setattr(slicer, "fetch_info", lambda url, ffmpeg: info)

    assert slicer.main() == 0

    assert "All chapter files already exist" in capsys.readouterr().out


def test_main_reports_download_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = make_args(tmp_path)

    def fail_download(url: str, ffmpeg: str, outdir: Path) -> tuple[Path, Path]:
        raise RuntimeError("download boom")

    monkeypatch.setattr(slicer, "parse_args", lambda: args)
    monkeypatch.setattr(slicer, "resolve_ffmpeg", lambda explicit=None: "/fake/ffmpeg")
    monkeypatch.setattr(
        slicer,
        "fetch_info",
        lambda url, ffmpeg: {"title": "My Video", "duration": 100},
    )
    monkeypatch.setattr(slicer, "download_source", fail_download)

    assert slicer.main() == 1

    assert "Error: download boom" in capsys.readouterr().err
