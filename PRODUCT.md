# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack
Vanilla JS, no build step. Static files in `src/fortevision/static/` (index.html, css/, js/) served by FastAPI, deployed on Modal. Tailwind via CDN is currently used and conflicts with the offline requirement below (open decision).

## Users
- Primary: a ForteBank controller / security officer who reviews cashier-desk video for possible cash-discipline violations and needs to see the exact moment, evidence and rule fast.
- Secondary: the ForteBank Datathon 26 jury, who open the UI and call the API with test videos (.mov, top-down camera, one till, up to ~145 s).

## Product Purpose
ForteVision analyses a cashier's video locally, flags visually observable signs of possible violations, and shows the type, timecode, evidence frames, a factual description and the cited NBRK regulation clause. Success: a reviewer verifies or dismisses each flag in seconds instead of watching the whole recording.

## Positioning
Every flag is tied to a frame, a timecode and a specific clause of an NB RK act (№ 231, № 14), and the whole pipeline runs on local open-source models, so it can move into the bank's closed network.

## Operating Context
Reviewer uploads a .mov, waits up to 5 minutes for analysis (cold GPU start can add minutes), then inspects results in a player. Videos are staged, top-down, one camera, no audio. The jury also scores via the REST API (`X-API-Key`), so the UI and API must agree.

## Capabilities and Constraints
- Verdict is exactly one of: no violation / signs of possible violations.
- Output is a hint for human review, not a legal or disciplinary conclusion; never claim what the video cannot show (amounts, postings, permissions).
- No face recognition or identification; analysis is visual only, no audio.
- Everything runs locally; no cloud AI services. After weights are downloaded no internet is needed for analysis.
- UI language is Russian.
- Evidence: 1-3 JPEG frames per violation. Violation types come from `regulations.py`.
- Open decision: remove CDN dependencies (Tailwind, Google Fonts) to honor offline operation.

## Brand Commitments
Name "ForteVision", a ForteBank Datathon 2026 product. ForteBank burgundy `#8B1832` is the brand colour and stays.

## Evidence on Hand
- Case brief: `Кейс_ForteBank.docx.pdf`.
- 42 open training videos in `open/videos/` with labels in `open/open_labels.xlsx` (22 violation, 20 none).
- Measured results in `evaluation_results.json` (training-set numbers, inflated; leave-one-out in README).
- Regulation sources listed in README. No testimonials, customers or production metrics exist; do not invent any.

## Product Principles
1. Show, don't assert: every claim links to a frame, a timecode and a clause.
2. A hint, never a verdict: wording stays cautious and invites human verification.
3. Missing a violation is worse than a false alarm, but false alarms must be easy to dismiss.
4. Honest about uncertainty: training-set metrics are labelled as such.
5. Works offline and on a closed network.

## Accessibility & Inclusion
No product-specific standard established. Reviewers work on desktop; the UI must stay usable by keyboard (player shortcuts exist) and on a phone.
