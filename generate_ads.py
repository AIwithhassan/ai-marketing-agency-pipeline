#!/usr/bin/env python3
"""
generate_ads.py - batch-generate vertical video ads with Dreamina Seedance 2.5
on BytePlus ModelArk.

What it does, in order:
  1. Reads campaign.json (one entry per ad) and checks every job and image.
  2. Prints a cost estimate table.
  3. With --dry-run, stops there. Otherwise it asks you to type "yes".
  4. Submits the jobs (never more than MAX_CONCURRENT_TASKS at once).
  5. Polls each task, printing a line whenever its status changes.
  6. Downloads finished videos to ./output/<job id>_<language>.mp4
  7. Prints actual tokens vs. the estimate, lists failures, writes run_log.json.

Usage:
    python generate_ads.py --dry-run    # cost table only, spends nothing
    python generate_ads.py              # cost table, confirmation, then run

Install:
    pip install "byteplus-python-sdk-v2[ark]" httpx sniffio python-dotenv requests

    httpx and sniffio are not used by this script directly. The BytePlus SDK
    imports them but (as of v3.0.61) does not declare them as dependencies,
    so we install them by hand.

Everything below follows the ModelArk docs (docs.byteplus.com, checked October 2026):
  - Create / Retrieve a video generation task (API reference)
  - Video generation tutorial, Dreamina Seedance 2.5 tutorial + prompt guide
  - Pricing, Region availability
"""

import argparse
import base64
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from byteplussdkarkruntime import Ark

# The SDK keeps its exception classes in a private module; this is where they live.
from byteplussdkarkruntime._exceptions import (
    ArkAPIConnectionError,  # network problem or timeout -> worth retrying
    ArkAPIStatusError,  # any 4xx / 5xx response
    ArkInternalServerError,  # 5xx -> worth retrying
    ArkRateLimitError,  # 429 Too Many Requests -> wait, then retry
)


# =============================================================================
# CONFIG - everything you might want to change lives here
# =============================================================================

# --- Model and region --------------------------------------------------------
# The console shows "Dreamina-Seedance-2.5"; the API needs this exact model ID.
MODEL_ID = "dreamina-seedance-2-5-260628"

# Asia Pacific (Johor) is region ID ap-southeast-1. This is its base URL.
# Your API key must be created in the same region, or calls will fail.
BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"

# --- Output format -------------------------------------------------------------
# These are sent as request-body parameters (the docs' recommended method),
# not as "--rs 1080p" style flags inside the prompt text.
RESOLUTION = "720p"  # "1080p" for final delivery, "720p" for cheaper test runs
ASPECT_RATIO = "9:16"  # vertical, for social placements
DURATION_SECONDS = 8  # Seedance 2.5 accepts 4-30 seconds
OUTPUT_FORMAT = "mp4"  # note: 1080p output is H.265/HEVC, 10-bit
GENERATE_AUDIO = True  # music, sound effects and any voiceover in the prompt
WATERMARK = False  # True adds an "AI Generated" mark bottom-right

# --- Pricing (USD per 1,000 tokens) --------------------------------------------
# From the ModelArk Pricing page, online inference, "input without video"
# (we only send images). The docs quote per million tokens: 10.70 for 480p
# and 720p, 11.70 for 1080p. Note 1080p is NOT 0.0107 - it costs more.
PRICE_PER_1K_TOKENS_USD = {
    "480p": 0.0107,
    "720p": 0.0107,
    "1080p": 0.0117,
}

# --- Quota ----------------------------------------------------------------------
# The API has no endpoint for remaining balance or resource-pack quota, so
# copy the number from the BytePlus console before each run.
QUOTA_REMAINING_USD = 50.00

# --- Rate limits and polling -----------------------------------------------------
# Docs: individual accounts may run 3 tasks at once (enterprise: 10).
# Extra tasks would just wait in BytePlus's queue, but we hold them back
# locally so the screen shows exactly what is generating.
MAX_CONCURRENT_TASKS = 3

POLL_INTERVAL_START = 10  # seconds before the first status check
POLL_INTERVAL_MAX = 60  # never wait longer than this between checks
POLL_BACKOFF_FACTOR = 1.5  # each check waits 1.5x longer than the last

RATE_LIMIT_WAIT_START = 5  # seconds to wait after a 429, doubling each time
RATE_LIMIT_WAIT_MAX = 120
MAX_SUBMIT_ATTEMPTS = 8  # give up on a job after this many retries

JOB_TIMEOUT_MINUTES = 30  # stop waiting for a task after this long
DOWNLOAD_ATTEMPTS = 3

# --- Files ----------------------------------------------------------------------
CAMPAIGN_FILE = Path("campaign.json")
IMAGES_DIR = Path("images")
OUTPUT_DIR = Path("output")
RUN_LOG_FILE = Path("run_log.json")


# =============================================================================
# FACTS FROM THE DOCS - you should not need to edit these
# =============================================================================

# Output pixel size for Seedance 2.5, by (resolution, aspect ratio).
VIDEO_SIZE = {
    ("480p", "16:9"): (854, 480),   ("720p", "16:9"): (1280, 720),   ("1080p", "16:9"): (1920, 1080),
    ("480p", "4:3"): (752, 560),    ("720p", "4:3"): (1112, 834),    ("1080p", "4:3"): (1664, 1248),
    ("480p", "1:1"): (640, 640),    ("720p", "1:1"): (960, 960),     ("1080p", "1:1"): (1440, 1440),
    ("480p", "3:4"): (560, 752),    ("720p", "3:4"): (834, 1112),    ("1080p", "3:4"): (1248, 1664),
    ("480p", "9:16"): (480, 854),   ("720p", "9:16"): (720, 1280),   ("1080p", "9:16"): (1080, 1920),
    ("480p", "21:9"): (992, 432),   ("720p", "21:9"): (1470, 630),   ("1080p", "21:9"): (2206, 946),
}
FRAMES_PER_SECOND = 24

# Prompt / native audio languages Seedance 2.5 officially supports.
# A job in any other language (German or French, say) gets a warning.
SUPPORTED_LANGUAGES = {"en", "zh", "es", "id", "ms", "th", "ar", "pt", "vi", "ja", "ko"}

# Image limits for reference images.
IMAGE_MIME_TYPES = {
    ".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp", ".bmp": "bmp",
    ".tif": "tiff", ".tiff": "tiff", ".gif": "gif", ".heic": "heic", ".heif": "heif",
}
MAX_IMAGE_BYTES = 30 * 1024 * 1024  # each image must be under 30 MB
MAX_REFERENCE_IMAGES = 30


# =============================================================================
# STEP 1 - load and check the campaign
# =============================================================================

def load_campaign(path):
    """Read campaign.json and return its list of jobs."""
    if not path.exists():
        sys.exit(f"Could not find {path}. Create it first (see README.md for the format).")
    with path.open(encoding="utf-8") as f:
        return json.load(f)["jobs"]


def check_jobs(jobs):
    """Find every problem up front, before any money is spent.

    Errors stop the run. Warnings are printed but do not stop it.
    """
    errors, warnings = [], []
    seen_ids = set()

    for job in jobs:
        job_id = job.get("id", "<missing id>")

        for key in ("id", "angle", "language", "prompt", "reference_images"):
            if not job.get(key):
                errors.append(f"{job_id}: missing '{key}'")
        if job_id in seen_ids:
            errors.append(f"{job_id}: duplicate id")
        seen_ids.add(job_id)

        images = job.get("reference_images") or []
        if not 1 <= len(images) <= MAX_REFERENCE_IMAGES:
            errors.append(f"{job_id}: needs 1 to {MAX_REFERENCE_IMAGES} reference images")
        for name in images:
            image_path = IMAGES_DIR / name
            if not image_path.exists():
                errors.append(f"{job_id}: image not found: {image_path}")
            elif image_path.suffix.lower() not in IMAGE_MIME_TYPES:
                errors.append(f"{job_id}: unsupported image type: {name}")
            elif image_path.stat().st_size >= MAX_IMAGE_BYTES:
                errors.append(f"{job_id}: image is 30 MB or larger: {name}")

        if job.get("language") not in SUPPORTED_LANGUAGES:
            warnings.append(
                f"{job_id}: language '{job.get('language')}' is not officially supported "
                "by Seedance 2.5 for prompts or speech. Keep the prompt in English and "
                "add the voiceover in post."
            )
        if "PLACEHOLDER" in job.get("prompt", ""):
            warnings.append(f"{job_id}: prompt still contains PLACEHOLDER text")

    for warning in warnings:
        print(f"  Warning - {warning}")
    if errors:
        print("\nThe campaign has problems. Nothing was submitted:")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)


# =============================================================================
# STEP 2 - estimate cost
# =============================================================================

def estimate_tokens():
    """Token estimate from the Pricing page.

    tokens = duration x width x height x frames per second / 1024
    (There is no input video here, so input video duration is 0.)
    """
    width, height = VIDEO_SIZE[(RESOLUTION, ASPECT_RATIO)]
    return int(DURATION_SECONDS * width * height * FRAMES_PER_SECOND / 1024)


def tokens_to_usd(tokens):
    return tokens / 1000 * PRICE_PER_1K_TOKENS_USD[RESOLUTION]


def print_cost_table(jobs):
    """Print one row per job, then the total and the quota left afterwards."""
    tokens_per_job = estimate_tokens()
    total_tokens = tokens_per_job * len(jobs)
    total_cost = tokens_to_usd(total_tokens)

    print("\nCost estimate")
    print(f"Model {MODEL_ID}, {RESOLUTION} {ASPECT_RATIO}, "
          f"${PRICE_PER_1K_TOKENS_USD[RESOLUTION]} per 1K tokens\n")
    header = f"{'Job':<8}{'Lang':<6}{'Angle':<20}{'Duration':>9}{'Resolution':>12}{'Est. tokens':>14}{'Est. cost':>11}"
    print(header)
    print("-" * len(header))
    for job in jobs:
        print(f"{job['id']:<8}{job['language']:<6}{job['angle']:<20}"
              f"{DURATION_SECONDS:>8}s{RESOLUTION:>12}"
              f"{tokens_per_job:>14,}{'$' + format(tokens_to_usd(tokens_per_job), '.2f'):>11}")
    print("-" * len(header))
    print(f"{'TOTAL':<55}{total_tokens:>14,}{'$' + format(total_cost, '.2f'):>11}")

    remaining_after = QUOTA_REMAINING_USD - total_cost
    print(f"\nQuota remaining (set by hand in the config): ${QUOTA_REMAINING_USD:.2f}")
    print(f"Quota remaining after this batch:            ${remaining_after:.2f}")
    if remaining_after < 0:
        print("Warning - this batch costs more than the quota you have left.")
    print("Estimates use the documented formula. Real usage comes back from the API per task.\n")
    return total_cost


# =============================================================================
# STEP 3 - build and submit a task
# =============================================================================

def image_to_data_url(path):
    """Turn a local image into a Base64 data URL, the format the API accepts:
    data:image/<format>;base64,<data>   (format must be lowercase)
    """
    image_format = IMAGE_MIME_TYPES[path.suffix.lower()]
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/{image_format};base64,{encoded}"


def build_content(job):
    """The prompt first, then each reference image in order.

    Order matters: the first image is "@Image 1" in the prompt, the second
    is "@Image 2", and so on.
    """
    content = [{"type": "text", "text": job["prompt"]}]
    for name in job["reference_images"]:
        content.append({
            "type": "image_url",
            "image_url": {"url": image_to_data_url(IMAGES_DIR / name)},
            "role": "reference_image",
        })
    return content


def submit_task(client, job):
    """Create one video generation task and return its task ID."""
    result = client.content_generation.tasks.create(
        model=MODEL_ID,
        content=build_content(job),
        # "reference" = a reference-to-video task. This is the only image
        # mode that lets us choose 9:16; first-frame mode forces "adaptive".
        omni_reference_task_type="reference",
        resolution=RESOLUTION,
        ratio=ASPECT_RATIO,
        duration=DURATION_SECONDS,
        output_format=OUTPUT_FORMAT,
        generate_audio=GENERATE_AUDIO,
        watermark=WATERMARK,
    )
    return result.id


# =============================================================================
# Bookkeeping for one job while the batch runs
# =============================================================================

@dataclass
class JobRun:
    job: dict
    estimated_tokens: int
    status: str = "waiting"  # waiting -> submitted -> queued -> running -> succeeded
    task_id: str | None = None
    error: str | None = None
    tokens_used: int | None = None
    output_path: str | None = None
    video_url: str | None = None  # kept in the log: valid for 24 hours
    # Timing details used by the scheduler (not written to the log)
    submit_attempts: int = 0
    submitted_at: float = 0.0
    next_poll_at: float = 0.0
    poll_interval: float = POLL_INTERVAL_START

    @property
    def label(self):
        return f"{self.job['id']} ({self.job['language']})"


def describe_api_error(error):
    """A short, readable error message (never includes the API key)."""
    code = getattr(error, "code", None) or getattr(error, "status_code", "")
    return f"{code}: {error.message}"


# Task statuses from the API, phrased to read well out loud.
STATUS_WORDS = {
    "queued": "is waiting in the BytePlus queue",
    "running": "is generating",
    "succeeded": "has finished",
    "failed": "has failed",
    "cancelled": "was cancelled",
    "expired": "expired before it could run",
}


def friendly_duration(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes} min {seconds} s" if minutes else f"{seconds} s"


# =============================================================================
# STEP 4 - run the batch: submit, poll, download
# =============================================================================

def try_submit(client, run):
    """Try to submit one job. Returns "submitted", "retry_later" or "failed"."""
    run.submit_attempts += 1
    print(f"Submitting {run.label}, {run.job['angle']} ...")
    try:
        run.task_id = submit_task(client, run.job)

    except (ArkRateLimitError, ArkInternalServerError, ArkAPIConnectionError) as error:
        # Temporary problems: put the job back in line instead of failing it.
        if run.submit_attempts >= MAX_SUBMIT_ATTEMPTS:
            run.status, run.error = "failed", f"gave up after {run.submit_attempts} attempts: {error}"
            print(f"  Giving up on {run.label}. {run.error}")
            return "failed"
        reason = "Rate limit reached" if isinstance(error, ArkRateLimitError) else "Temporary error"
        print(f"  {reason}. {run.label} stays in the queue.")
        return "retry_later"

    except ArkAPIStatusError as error:
        # 400, 401, 403 ... the request itself is wrong; retrying won't help.
        run.status, run.error = "failed", describe_api_error(error)
        print(f"  {run.label} was rejected: {run.error}")
        return "failed"

    run.status = "submitted"
    run.submitted_at = time.monotonic()
    run.next_poll_at = run.submitted_at + POLL_INTERVAL_START
    print(f"  Submitted. Task ID {run.task_id}")
    return "submitted"


def schedule_next_poll(run):
    """Back off: wait a little longer before each check, up to a maximum."""
    run.next_poll_at = time.monotonic() + run.poll_interval
    run.poll_interval = min(run.poll_interval * POLL_BACKOFF_FACTOR, POLL_INTERVAL_MAX)


def poll_once(client, run):
    """Check one task. Returns True once the job is finished (either way)."""
    elapsed = time.monotonic() - run.submitted_at
    try:
        task = client.content_generation.tasks.get(task_id=run.task_id)
    except (ArkRateLimitError, ArkInternalServerError, ArkAPIConnectionError):
        print(f"Polling: could not reach {run.label} just now. Trying again shortly.")
        schedule_next_poll(run)
        return False
    except ArkAPIStatusError as error:
        run.status, run.error = "failed", describe_api_error(error)
        print(f"Polling: {run.label} could not be retrieved: {run.error}")
        return True

    # Print a line only when the status changes, so the output stays readable.
    if task.status != run.status:
        run.status = task.status
        spoken = STATUS_WORDS.get(task.status, f"is {task.status}")
        print(f"Polling: {run.label} {spoken}. {friendly_duration(elapsed)} so far.")

    if task.status == "succeeded":
        run.tokens_used = task.usage.completion_tokens if task.usage else None
        run.video_url = task.content.video_url
        download_video(run)
        return True

    if task.status in ("failed", "cancelled", "expired"):
        run.error = f"{task.error.code}: {task.error.message}" if task.error else task.status
        print(f"  {run.label} did not finish: {run.error}")
        return True

    if elapsed > JOB_TIMEOUT_MINUTES * 60:
        run.status = "timed_out"
        run.error = (f"still {task.status} after {JOB_TIMEOUT_MINUTES} minutes; "
                     "it may still finish - look up the task ID later")
        print(f"  Stopped waiting for {run.label}: {run.error}")
        return True

    schedule_next_poll(run)
    return False


def download_video(run):
    """Save the finished video. The URL expires after 24 hours, so do it now."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"{run.job['id']}_{run.job['language']}.{OUTPUT_FORMAT}"
    partial = path.with_suffix(path.suffix + ".part")
    print(f"Downloading {run.label} to {path} ...")

    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            with requests.get(run.video_url, stream=True, timeout=120) as response:
                response.raise_for_status()
                with partial.open("wb") as f:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
            partial.replace(path)  # only a complete file gets the real name
            run.output_path = str(path)
            size_mb = path.stat().st_size / 1024 / 1024
            print(f"  Saved {path} ({size_mb:.1f} MB).")
            return
        except requests.RequestException as error:
            print(f"  Download attempt {attempt} failed: {error}")
            time.sleep(5 * attempt)

    run.status = "download_failed"
    run.error = "video generated but download failed; video_url in run_log.json works for 24 hours"
    print(f"  {run.error}")


def run_batch(client, runs):
    """Keep submitting and polling until every job has finished.

    One loop, no threads:
      1. Fill free slots with waiting jobs (respecting rate limits).
      2. Check any task whose next poll is due.
      3. Save the log, then sleep until the next thing is due.
    """
    waiting = list(runs)  # not yet submitted, in campaign order
    active = []  # submitted and still generating
    rate_limit_wait = RATE_LIMIT_WAIT_START
    submit_again_at = 0.0  # pause submissions after a rate limit

    while waiting or active:
        # 1. Submit new jobs while there is a free slot
        while (waiting and len(active) < MAX_CONCURRENT_TASKS
               and time.monotonic() >= submit_again_at):
            run = waiting[0]
            outcome = try_submit(client, run)
            if outcome == "retry_later":
                print(f"  Waiting {rate_limit_wait} seconds before submitting again.")
                submit_again_at = time.monotonic() + rate_limit_wait
                rate_limit_wait = min(rate_limit_wait * 2, RATE_LIMIT_WAIT_MAX)
                break
            waiting.pop(0)
            if outcome == "submitted":
                active.append(run)
                rate_limit_wait = RATE_LIMIT_WAIT_START

        # 2. Poll every task that is due for a check
        for run in list(active):
            if time.monotonic() >= run.next_poll_at and poll_once(client, run):
                active.remove(run)

        # 3. Save progress (so a crash never loses task IDs), then sleep
        save_run_log(runs)
        due_times = [run.next_poll_at for run in active]
        if waiting and len(active) < MAX_CONCURRENT_TASKS:
            due_times.append(submit_again_at)
        if due_times:
            time.sleep(max(1.0, min(due_times) - time.monotonic()))


# =============================================================================
# STEP 5 - report
# =============================================================================

def save_run_log(runs):
    log = {
        "model": MODEL_ID,
        "resolution": RESOLUTION,
        "aspect_ratio": ASPECT_RATIO,
        "duration_seconds": DURATION_SECONDS,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "jobs": [
            {
                "id": run.job["id"],
                "language": run.job["language"],
                "angle": run.job["angle"],
                "task_id": run.task_id,
                "status": run.status,
                "error": run.error,
                "estimated_tokens": run.estimated_tokens,
                "tokens_used": run.tokens_used,
                "output_path": run.output_path,
                "video_url": run.video_url,
            }
            for run in runs
        ],
    }
    RUN_LOG_FILE.write_text(json.dumps(log, indent=2), encoding="utf-8")


def print_summary(runs):
    estimated = sum(run.estimated_tokens for run in runs)
    actual = sum(run.tokens_used or 0 for run in runs)
    succeeded = [run for run in runs if run.output_path]
    failed = [run for run in runs if not run.output_path]

    print("\nBatch finished.")
    print(f"Videos saved: {len(succeeded)} of {len(runs)}")
    print(f"Estimated tokens: {estimated:>12,}   (${tokens_to_usd(estimated):.2f})")
    print(f"Actual tokens:    {actual:>12,}   (${tokens_to_usd(actual):.2f})")
    print("You are only billed for videos that generated successfully.")

    if failed:
        print("\nFailures:")
        for run in failed:
            print(f"  - {run.label}, task {run.task_id or 'not created'}: {run.error}")
    print(f"\nFull details are in {RUN_LOG_FILE}.")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Batch-generate Seedance 2.5 video ads.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the cost table and exit without spending anything")
    args = parser.parse_args()

    print(f"Reading {CAMPAIGN_FILE} ...")
    jobs = load_campaign(CAMPAIGN_FILE)
    print(f"Found {len(jobs)} jobs. Checking them ...")
    check_jobs(jobs)

    total_cost = print_cost_table(jobs)
    if args.dry_run:
        print("Dry run: nothing was submitted.")
        return

    # Load the API key from .env. We only check it exists - never print it.
    load_dotenv()
    api_key = os.environ.get("ARK_API_KEY")
    if not api_key:
        sys.exit("ARK_API_KEY is not set. Add it to your .env file.")

    answer = input(f"Type 'yes' to submit {len(jobs)} jobs for about ${total_cost:.2f}: ")
    if answer.strip().lower() != "yes":
        print("Okay, nothing was submitted.")
        return

    # max_retries=0: the SDK would otherwise retry quietly; we want every
    # retry to happen in run_batch, where you can see it.
    client = Ark(base_url=BASE_URL, api_key=api_key, max_retries=0)

    runs = [JobRun(job=job, estimated_tokens=estimate_tokens()) for job in jobs]
    print(f"\nStarting the batch. Up to {MAX_CONCURRENT_TASKS} videos generate at a time.\n")
    try:
        run_batch(client, runs)
    except KeyboardInterrupt:
        print("\nStopped by you. Tasks already submitted keep running on BytePlus "
              "and are billed if they succeed. Their task IDs are in the run log.")
    finally:
        save_run_log(runs)
    print_summary(runs)


if __name__ == "__main__":
    main()
