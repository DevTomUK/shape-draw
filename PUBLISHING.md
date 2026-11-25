# Publishing Shape Draw

Researched November 2026. Fees and rules change, so check each site before you upload.

## Where to put it

| Where | What it is | Cost to you | Use it for |
| --- | --- | --- | --- |
| **extensions.blender.org** | Blender's official extension site. Users can install straight from Edit > Preferences > Get Extensions | Free. Every upload is reviewed by moderators | **The main home.** It's the only place people find add-ons from inside Blender |
| **GitHub** (its own repo) | Source, issues and release zips | Free | The `website` link in the manifest, bug reports, and a download for people who don't use the extension site |
| **Gumroad** | Sell or give away direct, including "pay what you want" ($0+) | 10% + $0.50 per paid sale (30% if the buyer found it through Gumroad Discover). No monthly fee | Optional tip jar: a free download where people can choose to pay |
| **Superhive** (was Blender Market) | The big paid Blender marketplace | Free plan: 70% to you, plus a 5.5% merchant fee and $0.49 per sale. **Free products need the Pro plan ($49/month)** | Only once there's a paid version worth selling |
| **BlenderNation AdNet** | Free banner ads on BlenderNation and BlenderArtists, paid through an affiliate cut | Free, plus the affiliate % you set (15-30%) | Only for a paid product on Gumroad or Superhive |

**Recommendation:** release it free on extensions.blender.org, with a GitHub repo as its website. Add a Gumroad pay-what-you-want page if you'd like tips. Leave Superhive until there's something to charge for.

### Rules that matter for this add-on

- **extensions.blender.org** ([terms](https://extensions.blender.org/terms-of-service), [moderation guidelines](https://developer.blender.org/docs/features/extensions/moderation/guidelines/)):
  - The code must be GPL-3.0-or-later, and images must be CC0. Both are already the case here.
  - No "Blender" in the name, and no Blender logo in the icon or images. The screenshots are taken with the headers hidden for this reason.
  - Nothing inside Blender's UI may advertise anything: no donate or buy buttons, no "Pro" prompts.
  - The description and images may only show features in the published version. **If you ever make a paid version, the free listing can't show paid-only features.** A link to a donation page is allowed in the description.
  - No auto-updater, nothing downloaded at runtime, and no writing into the add-on's own folder. Shape Draw does none of these.
  - Reviewers run `ruff` on the code (see "Lint" below) and check that the add-on works.
  - Both an icon and a featured image are required before you can submit for review.
- **Superhive** ([guidelines](https://support.superhivemarket.com/article/253-general-product-guidelines-for-uploading-products), [pricing](https://superhivemarket.com/pricing)):
  - Licence must be GPL or MIT. Buyers get the source, and with GPL they're free to share it, so you're selling convenience and support.
  - It must be a valid extension with a unique id.
  - The product page needs written install steps and docs. A video on its own isn't enough.
  - The gallery needs at least 5 images at 2:1, at least 1200 px wide (a too-small gallery is the top rejection reason). The featured image is a 2:1 JPG under 5 MB.
  - You're expected to answer support messages within 72 hours. The first product takes 3-5 days to review.

## What's in this folder for each site

| Site field | File |
| --- | --- |
| Upload (zip) | `dist/shape_draw-<version>.zip` from `python make.py build` |
| Icon | `listing/icon.png` (256 x 256) |
| Featured image | extensions.blender.org: `listing/featured.png` (16:9) · Superhive: `listing/featured.jpg` (2:1) |
| Screenshots / gallery | `listing/screenshots/01..05-*.png` (2:1, 1920 x 960: five, Superhive's minimum) |
| Description | `listing/description.md` |
| Release notes | that version's section of `CHANGELOG.md` |
| Website / source | the GitHub repo URL. Also put it in `blender_manifest.toml` as `website` |

Regenerate the images after UI changes:

```
python listing/make_icon.py
"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --factory-startup --window-geometry 0 0 1920 1080 --python listing/make_screenshots.py
```

The screenshot script opens its own Blender window, takes the shots and closes again.

The README animation (`listing/demo.gif`, about 2.4 MB) is rebuilt with `python listing/make_demo.py` (plain Python with Pillow; it runs Blender itself). Use it on BlenderArtists and Reddit posts too, but not as the extensions.blender.org featured image: GIFs are discouraged there.

## Release steps

1. Bump the version in **both** `blender_manifest.toml` (`version = "0.2.0"`) and `shape_draw.py` (`bl_info["version"] = (0, 2, 0)`).
2. Add a `## 0.2.0` section to `CHANGELOG.md`.
3. Run `python make.py build`. It refuses to build if the versions don't match, the changelog entry is missing, Blender's validator complains, or a test fails.
4. Try the zip in Blender: Preferences > Get Extensions > ⌄ > Install from Disk.
5. Commit, then tag and push: `git tag v0.2.0` and `git push --follow-tags`. CI re-runs the checks on Blender 4.2 and 5.2, then publishes a GitHub release with the zip and the changelog section.
6. **extensions.blender.org**:
   - First time only: sign in with a Blender ID, then Upload Extension. Fill in the description, icon, featured image and screenshots, then submit for review.
   - Later versions: open the extension's page, choose Upload New Version, then paste the changelog section.
7. If you're on Gumroad or Superhive, upload the same zip there too.

## Getting it noticed (free)

- **BlenderArtists** forum, "Released Scripts and Themes" category: post a thread with a GIF and the link.
- **BlenderNation** news submission (blendernation.com, "Submit News"): they cover new add-ons.
- **r/blender**: a short screen recording does best.
- A 30-60 second video of drawing, curving and cutting. Link it from the description.

## Lint

The reviewers use ruff. Settings are in `pyproject.toml`:

```
pip install ruff
python make.py lint
```

## Before the first upload

- [x] Public GitHub repo, `website` set in `blender_manifest.toml`
- [x] `python make.py lint` is clean
- [ ] CI is green on GitHub (Blender 4.2 and 5.2)
- [ ] Install the built zip in a fresh Blender and draw a shape by hand
- [ ] Watch for the moderators' review comments and reply on the extension page
