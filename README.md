# Seedance Ad Pipeline

Batch-generate video ads for any product using the BytePlus Seedance 2.5 API. One command, one campaign file, a full set of ad variants in multiple languages.

> 👉 Get your Seedance 2.5 API key and grab a resource plan on BytePlus: **[LANDING PAGE LINK]**

📺 **Watch the full walkthrough:** [https://youtu.be/UEsyub42Vr4](https://www.youtube.com/watch?v=_L_d4g0ekh4&lc=Ugw4lgNch65tP2xW2-B4AaABAg)

---

## What this is

A single Python script that takes a campaign file and a folder of product images, and returns finished video ads. It was built for running an AI ad service: find a brand, generate a sample campaign for their product, send it to them.

It handles the parts that make batch generation annoying:

- Prints a cost estimate before spending anything, with a `--dry-run` flag
- Submits jobs within your account's concurrency limit instead of failing on rate limits
- Polls each task with backoff and prints readable status lines
- Downloads every video as soon as it's ready (URLs expire after 24 hours)
- Keeps going when a job fails, and logs everything to `run_log.json`
- Reports actual token spend against the estimate

## Requirements

- Python 3.10+
- A BytePlus account with an API key
- Credit or a resource plan on the account

## Setup

```bash
git clone https://github.com/AIwithhassan/ai-marketing-agency-pipeline
cd ai-marketing-agency-pipeline

pip install "byteplus-python-sdk-v2[ark]" httpx sniffio python-dotenv requests
```

Or, with [uv](https://docs.astral.sh/uv/): `uv sync`, then prefix the commands below with `uv run`.

Create a `.env` file:

```
ARK_API_KEY=your_key_here
```

Put your product images in `./images/`. The included `campaign.json` is an example for a whey protein pouch and expects `whey-1.jpg` and `whey-2.jpg`. Change `reference_images` to your own filenames.

## Before your first run

Two console settings will block your first API call. Both are in the BytePlus console:

1. **Model activation.** Each model has to be activated individually. Creating an API key is not enough. Activate Dreamina-Seedance-2.5 in the same region your key was created in.
2. **Safe Experience Mode.** New accounts have a low per-model spending cap that pauses the service once hit. Raise the limit or switch the mode off on the Model Activation page.

If you skip these you'll see `ModelNotOpen` or `SetLimitExceeded`.

## Usage

```bash
# Cost table only, spends nothing
python generate_ads.py --dry-run

# Full run, asks for confirmation before submitting
python generate_ads.py
```

Videos land in `./output/`.

## Configuration

Everything you'd want to change is at the top of `generate_ads.py`:

| Setting | Default | Notes |
|---|---|---|
| `RESOLUTION` | `720p` | Switch to `1080p` for final delivery. 720p is roughly 60% cheaper |
| `ASPECT_RATIO` | `9:16` | Vertical, for social placements |
| `DURATION_SECONDS` | `8` | Seedance 2.5 accepts 4–30s |
| `MAX_CONCURRENT_TASKS` | `3` | Individual account limit |
| `QUOTA_REMAINING_USD` | `50.00` | Copy from the console before each run; the API doesn't expose balance |

## The campaign file

`campaign.json` holds one entry per ad:

```json
{
  "jobs": [
    {
      "id": "ad01",
      "angle": "product_benefit",
      "language": "en",
      "prompt": "Your prompt here, referencing @Image 1 and @Image 2",
      "reference_images": ["product-1.jpg", "product-2.jpg"]
    }
  ]
}
```

The first file in `reference_images` is `@Image 1` in the prompt, the second is `@Image 2`, and so on.

### Prompt tips

- Tell it explicitly not to reproduce text, badges or graphics from your reference images
- Keep spoken lines to one short clause — long sentences generate unreliably
- Always add "no subtitles, no captions, no on-screen text" — text rendering is the most common failure
- Describe the product's shape, colours and proportions so it stays recognisable

### Supported languages

English, Chinese, Spanish, Indonesian, Malay, Thai, Arabic, Portuguese, Vietnamese, Japanese, Korean.

German, French and Italian are not on the list. For those, generate in English with music and sound effects only, then add voiceover in post. The script warns you about unsupported languages.

---

## Finding brands to pitch

The workflow starts with finding advertisers who are spending on paid creative but running weak assets. Public ad libraries make this free.

### TikTok Commercial Content Library
**https://library.tiktok.com/ads**

The most detailed of the free options — searchable by keyword or advertiser, with first-shown and last-shown dates, declared targeting, and reach bands. No account needed.

**Coverage is EEA, Switzerland and the UK only.** This is a DSA transparency requirement, so there's no equivalent elsewhere. Searches for other countries return nothing.

### For every other country

| Library | URL | Coverage |
|---|---|---|
| Meta Ad Library | https://www.facebook.com/ads/library/ | Global. Facebook and Instagram. No login needed — set category to "All ads", it defaults to political |
| Google Ads Transparency Center | https://adstransparency.google.com/ | Global. Search, Display, YouTube |
| LinkedIn Ad Library | https://www.linkedin.com/ad-library/ | Global. Best for B2B |
| TikTok Creative Center | https://ads.tiktok.com/business/creativecenter/ | Global, but curated top-performing ads only, not a full library. Log in or you'll see very few |
| Meta Ad Library (EU view) | https://www.facebook.com/ads/library/ | EU results include extra DSA-mandated targeting and reach data |

### Reading "winning" without spend data

None of these show ad spend. Use proxies:

- **Run duration.** Long-running ads are generally ones the advertiser has kept investing in
- **Variant count.** The same creative in many versions suggests they're scaling it
- **Re-runs.** A creative that comes back after a gap was worth returning to
- **Reach band.** Where shown, high reach means real budget

Look for sustained investment paired with weak creative — static images, or basic video. That's the opening.

---

## Reaching out

Lead with the sample, not the pitch. One real observation about their ads, the videos, one question. Replace everything in `{curly braces}`.

### Email

```
Subject: {N} video ads for {Product}, made from your product shots

Hi {First name},

I was looking at {Brand}'s ads in the {Meta / TikTok} Ad Library. Your
{product} ad has been running since {month}, so it's clearly working,
but it's a static image.

I made {N} short vertical video versions to show what it could look like:

{private link to the videos}

- {Angle 1}, 8s, {English}
- {Angle 2}, 8s, {Spanish}
- {Angle 3}, 8s, {English}

They're AI-generated, which means I can turn around a full set of
angles and languages in a few days, at a fraction of a shoot's cost.

If you'd like a set made to your brief for {upcoming launch / season},
I'm happy to quote. Would a 15-minute call next week work?

Best,
{Your name}
{Website} · {Phone}

Not relevant? Just reply "no thanks" and I won't follow up. If you'd
rather I didn't use your product imagery, say so and I'll delete the
samples.
```

**Follow-up**, 4–5 days later, as a reply in the same thread:

```
Hi {First name}, a quick nudge on the {Product} videos: {link}

Happy to make a version for {a specific market / angle / platform}
if that's more useful. If the timing's wrong, no problem, just let
me know.
```

### Instagram / Facebook DM

DMs get read on a phone, so keep it short and attach the best video directly instead of a link.

```
Hi {Brand} team 👋 I made this 8-second video ad for {Product} from
your product shots (attached, AI-generated). Your current ads are
mostly static, so I thought you'd like to see it moving.

I have {2} more versions, including one in {Spanish}. Who's the best
person to send them to?
```

When they reply:

```
Thanks {Name}! Here's the full set: {private link}

I make these to brief: {N} angles × {N} languages, delivered in
{X} days. Want me to put together a quote for {Product} or your
next launch?
```

### Outreach tips

- **Personalise the first line** from what you actually saw in the ad library. Generic openers get ignored
- **Share samples privately** (unlisted link or a shared folder). They show another brand's product, so never post them publicly or use them in your own marketing without permission
- **Say they're AI-generated.** It's honest, and it's part of the pitch: speed and cost
- **Transcode before attaching.** 1080p output is HEVC, which some phones and inboxes won't preview. Convert to H.264 (`ffmpeg -i in.mp4 -c:v libx264 -pix_fmt yuv420p -crf 20 -c:a aac out.mp4`)
- **Check the cold-outreach rules** where you and the brand are based. GDPR applies in the EU, Germany's UWG §7 restricts unsolicited marketing email even B2B, and the US CAN-SPAM Act requires an opt-out. Use business addresses and stop when asked

---

## Cost

Pricing is per token, and token count scales with resolution and duration. At the time of writing, an 8-second 9:16 video works out to roughly:

- 720p — about $1.85 pay-as-you-go
- 1080p — about $4.55 pay-as-you-go

A prepaid resource plan brings the per-video cost down significantly. Run `--dry-run` for exact numbers at your settings, and check the current rates in the console since pricing changes.

> 👉 Grab a Seedance 2.5 resource plan on BytePlus: **[LANDING PAGE LINK]**

## Notes

- 1080p output is H.265/HEVC 10-bit. Some editors and ad platforms need a transcode
- Video URLs expire 24 hours after generation. The script downloads immediately, and keeps URLs in `run_log.json` as a fallback
- Your API key must be created in the same region as your activated model

## Responsible use

Generate from product imagery you own or have permission to use. Don't use another brand's advertising footage as generation input — study the structure and rebuild it instead. Don't put product claims in an ad unless you can substantiate them from the brand's own materials, and don't write synthetic testimonials.

## License

MIT, see [LICENSE](LICENSE).
