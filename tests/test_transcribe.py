"""Tests for scripts/transcribe.py. Never touch a real network download,
ffmpeg binary, or OpenAI call -- every test monkeypatches at those three
boundaries (download, the ffmpeg_* primitives, vision_call/audio_call),
same pattern as test_collect.py mocking run_actor.
"""
import json

import pytest

import transcribe as tc


def _row(creative_id, format_="IMAGE", media_urls=None, needs=True):
    if media_urls is None:
        media_urls = ["https://example.com/x.jpg"]
    return {"creative_id": creative_id, "format": format_,
            "media_urls": media_urls, "needs_transcription": needs}


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "CACHE_DIR", tmp_path / "transcript_cache")


# --------------------------------------------------------------- image


def test_process_image_row_downloads_and_sends_base64(monkeypatch):
    """Verified live: passing a bare media URL to OpenAI fails with
    invalid_image_url (their server-side fetch can't get past whatever
    fbcdn.net wants that a normal User-Agent satisfies). So collect.py
    downloads the image itself and sends it as base64 -- this test
    locks that behavior in rather than regressing to the URL-passthrough
    design that failed against a real fixture URL."""
    captured = {}

    def fake_download(url, timeout=30):
        captured["downloaded_url"] = url
        return b"\xff\xd8\xfake-jpeg-bytes"

    def fake_vision_call(blocks):
        captured["blocks"] = blocks
        return {"on_screen_text": "SAVE 20%", "description": "a coupon graphic"}

    monkeypatch.setattr(tc, "download", fake_download)
    monkeypatch.setattr(tc, "vision_call", fake_vision_call)

    row = _row("c1", media_urls=["https://cdn.example/ad.jpg"])
    result, cost = tc.process_image_row(row)

    assert captured["downloaded_url"] == "https://cdn.example/ad.jpg"
    block = captured["blocks"][0]
    assert block["type"] == "image_url"
    assert block["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "SAVE 20%" in result["transcript"]
    assert "coupon graphic" in result["transcript"]
    assert result["transcript_source"] == "ocr"
    assert cost == tc.IMAGE_VISION_COST


def test_process_image_row_with_no_media_urls_is_graceful(monkeypatch):
    monkeypatch.setattr(tc, "vision_call", lambda blocks: (_ for _ in ()).throw(
        AssertionError("should not be called")))
    row = _row("c2", media_urls=[])
    result, cost = tc.process_image_row(row)
    assert result["transcript"] == ""
    assert cost == 0.0


# --------------------------------------------------------------- video


def test_plan_video_row_uses_real_measured_duration(monkeypatch):
    monkeypatch.setattr(tc, "download", lambda url, timeout=30: b"fake-bytes")
    monkeypatch.setattr(tc, "ffprobe_duration", lambda path: 42.0)

    plan = tc.plan_video_row(_row("v1", format_="VIDEO"))
    assert plan["duration"] == 42.0
    expected_audio_cost = (42.0 / 60) * tc.AUDIO_COST_PER_MINUTE
    assert plan["cost"] == pytest.approx(tc.VIDEO_VISION_COST + expected_audio_cost)


def test_plan_video_row_no_media_urls_returns_none():
    assert tc.plan_video_row(_row("v2", format_="VIDEO", media_urls=[])) is None


def test_process_video_plan_combines_ocr_and_asr(monkeypatch, tmp_path):
    video_path = tmp_path / "v.mp4"
    video_path.write_bytes(b"x")
    monkeypatch.setattr(tc, "ffmpeg_extract_frame", lambda path, ts, out: Path_touch(out))
    monkeypatch.setattr(tc, "ffmpeg_extract_audio", lambda path, out: (Path_touch(out), True)[1])
    monkeypatch.setattr(tc, "vision_call", lambda blocks: {
        "on_screen_text": "50% OFF TODAY", "description": "a woman smiling"})
    monkeypatch.setattr(tc, "audio_call", lambda path: "Come see the new lineup at your dealer.")

    plan = {"video_path": str(video_path), "duration": 12.0}
    result = tc.process_video_plan(plan)

    assert result["transcript_source"] == "both"
    assert "50% OFF TODAY" in result["transcript"]
    assert "woman smiling" in result["transcript"]
    assert "dealer" in result["transcript"]
    assert not video_path.exists()   # cleaned up


def test_process_video_plan_source_is_ocr_when_no_audio_track(monkeypatch, tmp_path):
    video_path = tmp_path / "v2.mp4"
    video_path.write_bytes(b"x")
    monkeypatch.setattr(tc, "ffmpeg_extract_frame", lambda path, ts, out: Path_touch(out))
    monkeypatch.setattr(tc, "ffmpeg_extract_audio", lambda path, out: False)   # no audio stream
    monkeypatch.setattr(tc, "vision_call", lambda blocks: {
        "on_screen_text": "NEW", "description": "a logo"})

    def fail_audio_call(*a, **kw):
        raise AssertionError("must not call Whisper when there is no audio")
    monkeypatch.setattr(tc, "audio_call", fail_audio_call)

    plan = {"video_path": str(video_path), "duration": 5.0}
    result = tc.process_video_plan(plan)
    assert result["transcript_source"] == "ocr"


def test_process_video_plan_source_is_asr_when_no_onscreen_text(monkeypatch, tmp_path):
    video_path = tmp_path / "v3.mp4"
    video_path.write_bytes(b"x")
    monkeypatch.setattr(tc, "ffmpeg_extract_frame", lambda path, ts, out: Path_touch(out))
    monkeypatch.setattr(tc, "ffmpeg_extract_audio", lambda path, out: (Path_touch(out), True)[1])
    monkeypatch.setattr(tc, "vision_call", lambda blocks: {
        "on_screen_text": "", "description": ""})
    monkeypatch.setattr(tc, "audio_call", lambda path: "This is the whole message, spoken.")

    plan = {"video_path": str(video_path), "duration": 8.0}
    result = tc.process_video_plan(plan)
    assert result["transcript_source"] == "asr"
    assert result["transcript"].strip() == "This is the whole message, spoken."


def Path_touch(p):
    from pathlib import Path
    Path(p).touch()


# ------------------------------------------------------------ caching


def test_transcribe_all_skips_cached_rows_with_no_download_or_calls(monkeypatch, tmp_path):
    tc._save_cache("cached1", {"transcript": "already done",
                               "transcript_source": "ocr", "cost": 0.0003})

    def fail(*a, **kw):
        raise AssertionError("cached rows must not trigger a call")
    monkeypatch.setattr(tc, "vision_call", fail)
    monkeypatch.setattr(tc, "download", fail)

    row = _row("cached1")
    done = tc.transcribe_all([row])
    assert done == 1
    assert row["transcript"] == "already done"
    assert row["transcript_source"] == "ocr"


def test_transcribe_all_caches_after_processing(monkeypatch):
    monkeypatch.setattr(tc, "download", lambda url, timeout=30: b"fake-bytes")
    monkeypatch.setattr(tc, "vision_call", lambda blocks: {
        "on_screen_text": "text", "description": "desc"})

    row = _row("newimg1")
    done = tc.transcribe_all([row])
    assert done == 1
    cached = tc._load_cached("newimg1")
    assert cached["transcript_source"] == "ocr"

    # A second run must not call vision_call again.
    def fail(*a, **kw):
        raise AssertionError("should be served from cache on rerun")
    monkeypatch.setattr(tc, "vision_call", fail)
    row2 = _row("newimg1")
    done2 = tc.transcribe_all([row2])
    assert done2 == 1
    assert row2["transcript_source"] == "ocr"


def test_transcribe_all_ignores_rows_not_needing_transcription(monkeypatch):
    monkeypatch.setattr(tc, "vision_call", lambda blocks: (_ for _ in ()).throw(
        AssertionError("should not be called")))
    row = _row("skip1", needs=False)
    done = tc.transcribe_all([row])
    assert done == 0
    assert "transcript" not in row


def test_transcribe_all_keeps_partial_results_on_one_row_failure(monkeypatch):
    monkeypatch.setattr(tc, "download",
                        lambda url, timeout=30: b"BAD" if "bad" in url else b"GOOD")

    def flaky_vision_call(blocks):
        import base64
        data_url = blocks[0]["image_url"]["url"]
        raw = base64.b64decode(data_url.split(",", 1)[1])
        if raw == b"BAD":
            raise RuntimeError("model overloaded")
        return {"on_screen_text": "ok", "description": "fine"}

    monkeypatch.setattr(tc, "vision_call", flaky_vision_call)

    good = _row("good1", media_urls=["https://cdn.example/good.jpg"])
    bad = _row("bad1", media_urls=["https://cdn.example/bad.jpg"])
    done = tc.transcribe_all([good, bad])

    assert done == 1
    assert good.get("transcript_source") == "ocr"
    assert "transcript_source" not in bad


# ----------------------------------------------------------- estimate


def test_estimate_rough_counts_images_videos_and_cached(monkeypatch):
    tc._save_cache("already", {"transcript": "x", "transcript_source": "ocr", "cost": 0.0})
    rows = [
        _row("already"),
        _row("img1", format_="IMAGE"),
        _row("vid1", format_="VIDEO"),
        _row("nope", needs=False),
    ]
    est = tc.estimate_rough(rows)
    assert est["n_cached"] == 1
    assert est["n_images"] == 1
    assert est["n_videos"] == 1
    assert est["total"] > 0
