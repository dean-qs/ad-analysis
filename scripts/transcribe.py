"""Phase 2: transcribe creative for rows where copy is empty and the
message lives in the media itself.

def transcribe_all(rows: list) -> int

Only rows where `needs_transcription` is true. Two paths:

- Image: download it ourselves and send it to a vision model as base64.
  Verified live that passing the bare URL does not work reliably --
  OpenAI's server-side fetch failed with `invalid_image_url` on a real
  fbcdn.net URL we could download fine directly with a normal
  User-Agent. Extract all on-screen text plus a one-sentence description.
- Video: download locally (ffmpeg needs a file), sample 3 frames evenly
  across the real measured duration, extract audio, and transcribe it.
  The 3 frames go in a single vision call together, not three separate
  calls -- token cost is the same either way (pricing is per-token, not
  per-request), and one call means the model can write one coherent
  on-screen-text/description pair instead of three fragments to stitch.

Every row gets `transcript` and `transcript_source` (`ocr`, `asr`, or
`both`). Cached on disk by `creative_id` under out/transcript_cache/, so
a rerun costs nothing for rows already done -- no download, no API call.

This is the expensive stage per CLAUDE.md's standing rule: no paid call
without an itemized estimate first. Unlike collect.py's Apify calls,
video duration can be measured for free before any OpenAI call happens
(downloading is bandwidth, not billed), so the itemized report below
uses real measured durations, not a cap-based worst case. transcribe_all
itself does not block on an interactive confirmation, matching
collect_all's precedent: the Streamlit app's displayed estimate + button
click is the confirmation for that path, and main() below provides a
real prompt for command-line use.
"""
import json
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import imageio_ffmpeg

CACHE_DIR = Path("out/transcript_cache")

# gpt-4o-mini: mechanical extraction (read text, describe briefly), not
# creative reasoning -- the cheapest capable model is the right call
# here, especially since this stage can be a brand's entire corpus.
VISION_MODEL = "gpt-4o-mini"
VISION_COST_IN_PER_TOKEN = 0.15 / 1_000_000
VISION_COST_OUT_PER_TOKEN = 0.60 / 1_000_000

# gpt-4o-mini-transcribe: half the price of whisper-1, newer model.
AUDIO_MODEL = "gpt-4o-mini-transcribe"
AUDIO_COST_PER_MINUTE = 0.003

# Assumed token counts for a single high-detail image + instructions.
# Not measured per-image (dimension-based tiling math would require a
# download pass for a sub-cent cost that isn't worth the complexity).
# A video's single combined-frame call sends 3 images in one request.
IMAGE_TOKENS_IN = 1000
IMAGE_TOKENS_OUT = 250
VIDEO_TOKENS_IN = 3 * IMAGE_TOKENS_IN
VIDEO_TOKENS_OUT = 350

IMAGE_VISION_COST = (IMAGE_TOKENS_IN * VISION_COST_IN_PER_TOKEN
                     + IMAGE_TOKENS_OUT * VISION_COST_OUT_PER_TOKEN)
VIDEO_VISION_COST = (VIDEO_TOKENS_IN * VISION_COST_IN_PER_TOKEN
                     + VIDEO_TOKENS_OUT * VISION_COST_OUT_PER_TOKEN)

# Used only for the no-download, pre-click UI estimate (estimate_rough).
# transcribe_all itself measures every video's real duration for free
# before spending anything, so this assumption never affects billing.
ASSUMED_VIDEO_SECONDS = 30

OCR_PROMPT = (
    "This is an advertisement. Extract every piece of on-screen text "
    "verbatim, then write one sentence describing what is depicted. "
    "Respond as JSON: {\"on_screen_text\": str, \"description\": str}. "
    "If there is no on-screen text, use an empty string.")


def _cache_path(creative_id):
    return CACHE_DIR / f"{creative_id}.json"


def _load_cached(creative_id):
    p = _cache_path(creative_id)
    if p.exists():
        return json.loads(p.read_text())
    return None


def _save_cache(creative_id, result):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(creative_id).write_text(json.dumps(result, indent=1))


def download(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ------------------------------------------------------------- ffmpeg


def ffprobe_duration(path):
    """Seconds, via ffmpeg's own stderr (no separate ffprobe binary
    needed -- imageio-ffmpeg only bundles ffmpeg)."""
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    r = subprocess.run([exe, "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
    if not m:
        raise RuntimeError(f"could not read duration from ffmpeg output for {path}")
    h, mn, s = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(s)


def ffmpeg_extract_frame(path, timestamp, out_path):
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([exe, "-y", "-ss", str(timestamp), "-i", str(path),
                    "-frames:v", "1", "-q:v", "2", str(out_path)],
                   capture_output=True, check=True)


def ffmpeg_extract_audio(path, out_path):
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    r = subprocess.run([exe, "-y", "-i", str(path), "-vn", "-acodec",
                        "libmp3lame", "-q:a", "4", str(out_path)],
                       capture_output=True)
    if r.returncode != 0 or not Path(out_path).exists():
        return False   # no audio stream, or extraction failed -- not fatal
    return True


# --------------------------------------------------------- OpenAI calls
# Thin, monkeypatchable wrappers -- transcribe_all never touches the
# OpenAI client directly, matching collect.py's run_actor pattern.


def _client():
    import openai
    return openai.OpenAI()


def vision_call(content_blocks):
    """content_blocks: list of {"type": "image_url", "image_url": {"url": ...}}
    blocks, already built by the caller. Returns parsed
    {"on_screen_text": str, "description": str}."""
    resp = _client().chat.completions.create(
        model=VISION_MODEL,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": [{"type": "text", "text": OCR_PROMPT}]
                   + content_blocks}],
    )
    return json.loads(resp.choices[0].message.content)


def audio_call(audio_path):
    with open(audio_path, "rb") as f:
        resp = _client().audio.transcriptions.create(model=AUDIO_MODEL, file=f)
    return resp.text


# ------------------------------------------------------------- per-row


def _b64_image_block(path):
    import base64
    data = base64.b64encode(Path(path).read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}


def _b64_bytes_block(data: bytes):
    import base64
    return {"type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(data).decode()}"}}


def process_image_row(row):
    """Downloads the image ourselves and sends it as base64. Passing the
    bare media URL to OpenAI does not work reliably -- verified live: a
    real fbcdn.net URL that we can download directly with a normal
    User-Agent came back `invalid_image_url` / `Error while downloading`
    when OpenAI's servers tried to fetch it themselves, almost certainly
    because their fetcher doesn't send whatever header fbcdn is checking
    for. Downloading is free either way, so do it consistently for every
    source rather than trusting each CDN to be fetchable server-side.
    Returns (result_dict, cost)."""
    url = (row.get("media_urls") or [None])[0]
    if not url:
        return {"transcript": "", "transcript_source": "ocr",
                "note": "no media_urls to transcribe"}, 0.0
    image_bytes = download(url)
    parsed = vision_call([_b64_bytes_block(image_bytes)])
    text, desc = parsed.get("on_screen_text", ""), parsed.get("description", "")
    transcript = "\n\n".join(t for t in (text.strip(), f"[{desc.strip()}]") if t.strip("[] "))
    return {"transcript": transcript, "transcript_source": "ocr"}, IMAGE_VISION_COST


def plan_video_row(row):
    """Downloads the video and measures its real duration -- for free,
    before any OpenAI call. Returns a plan dict with the exact cost, or
    None if there is nothing to process."""
    url = (row.get("media_urls") or [None])[0]
    if not url:
        return None
    video_bytes = download(url)
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.write(video_bytes)
    tmp.close()
    duration = ffprobe_duration(tmp.name)
    audio_cost = (duration / 60) * AUDIO_COST_PER_MINUTE
    return {"row": row, "video_path": tmp.name, "duration": duration,
            "cost": VIDEO_VISION_COST + audio_cost}


def process_video_plan(plan):
    """Executes a plan built by plan_video_row: extract 3 frames evenly,
    one combined vision call, extract + transcribe audio. Cleans up its
    temp files. Returns a result dict; cost is whatever plan['cost']
    already said (measured before this ran)."""
    path, duration = plan["video_path"], plan["duration"]
    frame_paths, audio_path = [], None
    try:
        for frac in (1 / 6, 1 / 2, 5 / 6):
            fp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            fp.close()
            ffmpeg_extract_frame(path, duration * frac, fp.name)
            frame_paths.append(fp.name)

        parsed = vision_call([_b64_image_block(p) for p in frame_paths])
        ocr_text = parsed.get("on_screen_text", "").strip()
        description = parsed.get("description", "").strip()

        audio_path = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
        has_audio = ffmpeg_extract_audio(path, audio_path)
        asr_text = audio_call(audio_path).strip() if has_audio else ""

        pieces = [t for t in (ocr_text, f"[{description}]" if description else "",
                              asr_text) if t.strip("[] ")]
        transcript = "\n\n".join(pieces)
        if ocr_text and asr_text:
            source = "both"
        elif asr_text:
            source = "asr"
        else:
            source = "ocr"
        return {"transcript": transcript, "transcript_source": source}
    finally:
        for p in frame_paths:
            Path(p).unlink(missing_ok=True)
        Path(path).unlink(missing_ok=True)
        if audio_path:
            Path(audio_path).unlink(missing_ok=True)


# ---------------------------------------------------------- estimates


def estimate_rough(rows):
    """No downloads -- for a pre-click UI estimate before the run.
    Video duration is an assumption (ASSUMED_VIDEO_SECONDS), clearly
    flagged as such; transcribe_all measures the real thing instead."""
    todo = [r for r in rows if r.get("needs_transcription")
            and not _load_cached(r.get("creative_id"))]
    n_images = sum(1 for r in todo if r.get("format") != "VIDEO")
    n_videos = sum(1 for r in todo if r.get("format") == "VIDEO")
    video_audio_cost = (ASSUMED_VIDEO_SECONDS / 60) * AUDIO_COST_PER_MINUTE
    image_total = n_images * IMAGE_VISION_COST
    video_total = n_videos * (VIDEO_VISION_COST + video_audio_cost)
    return {
        "n_images": n_images, "n_videos": n_videos,
        "n_cached": sum(1 for r in rows if r.get("needs_transcription")
                        and _load_cached(r.get("creative_id"))),
        "image_cost": round(image_total, 4), "video_cost": round(video_total, 4),
        "total": round(image_total + video_total, 4),
        "assumed_video_seconds": ASSUMED_VIDEO_SECONDS,
    }


def transcribe_all(rows: list) -> int:
    todo = [r for r in rows if r.get("needs_transcription")]
    cached_rows, pending = [], []
    for r in todo:
        cached = _load_cached(r.get("creative_id"))
        if cached:
            r["transcript"] = cached["transcript"]
            r["transcript_source"] = cached["transcript_source"]
            cached_rows.append(r)
        else:
            pending.append(r)

    print(f"{len(cached_rows)} of {len(todo)} already cached, $0 to re-fetch")
    if not pending:
        return len(cached_rows)

    plans = []
    for r in pending:
        if r.get("format") == "VIDEO":
            plan = plan_video_row(r)
            if plan:
                plans.append(plan)
            else:
                print(f"  ! {r.get('creative_id')}: no media_urls, skipping")
        else:
            plans.append({"row": r, "cost": IMAGE_VISION_COST})

    total = round(sum(p["cost"] for p in plans), 4)
    print(f"\nItemized cost for this run ({len(plans)} creative(s), real "
          f"measured video durations, not assumed):")
    for p in plans:
        r = p["row"]
        kind = "video" if "duration" in p else "image"
        dur_note = f", {p['duration']:.1f}s" if "duration" in p else ""
        print(f"  {r.get('creative_id')}: {kind}{dur_note}  ~${p['cost']:.4f}")
    print(f"\n  total: ~${total:.4f}\n")

    done = len(cached_rows)
    running_cost = 0.0
    for p in plans:
        r = p["row"]
        cid = r.get("creative_id")
        try:
            if "duration" in p:
                result = process_video_plan(p)
            else:
                result, _ = process_image_row(r)
            r["transcript"] = result["transcript"]
            r["transcript_source"] = result["transcript_source"]
            _save_cache(cid, {"transcript": result["transcript"],
                              "transcript_source": result["transcript_source"],
                              "cost": p["cost"]})
            running_cost += p["cost"]
            done += 1
            print(f"  . {cid} done, running total ~${running_cost:.4f}")
        except Exception as e:  # noqa: BLE001
            print(f"  ! {cid} FAILED: {str(e)[:200]}")

    return done


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="out/ads_normalized.json")
    a = ap.parse_args()

    rows = json.loads(Path(a.in_path).read_text())
    est = estimate_rough(rows)
    print(f"Rough estimate (no downloads yet): {est['n_images']} image(s) "
          f"~${est['image_cost']:.2f}, {est['n_videos']} video(s) (duration "
          f"assumed {est['assumed_video_seconds']}s each) ~${est['video_cost']:.2f}, "
          f"{est['n_cached']} already cached")
    print(f"Rough total: ~${est['total']:.2f}\n"
          f"(transcribe_all measures real video durations before spending -- "
          f"the number it prints will differ from this.)")
    ans = input("\nProceed with this live run? [y/N] ").strip().lower()
    if ans != "y":
        print("Aborted, nothing spent.")
        return

    done = transcribe_all(rows)
    Path(a.in_path).write_text(json.dumps(rows, indent=1))
    print(f"\nTranscribed {done}, wrote {a.in_path}")


if __name__ == "__main__":
    main()
