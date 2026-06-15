# Architecture Decision Records — Fall Guardian v3

Each entry captures one significant design decision: the context it was made in, what was decided, what alternatives were on the table, and what consequences follow. ADRs are append-only — if a later decision overturns an earlier one, that earlier ADR stays in place (status: Superseded by ADR-N) and the new ADR cites it.

For the chronological story of how these decisions emerged, see [`BUILD_LOG.md`](BUILD_LOG.md).

---

## ADR-001 — Rebuild from scratch instead of patching v1 / v2

**Status**: Accepted (2026-05-28)

**Context.** Two earlier prototypes exist: `fall-detect-system` (ESP32 + Flask + RandomForest + Firebase + Flutter, my 2nd-year engineering project) and `fall-simulated` (sibling with a Python virtual device, web dashboard, auth, device pairing). Both function end-to-end and are deployed on Render. The audit ([`AUDIT_v1_v2.md`](AUDIT_v1_v2.md)) surfaced 20+ defects across ML, security, mobile UX, and DevOps.

**Decision.** Build v3 from scratch in a new monorepo (`fall-guardian/`) rather than patching either existing repo. The GitHub home of the project stays the `fall-detect-system` repository (preserves the engineering-project history); the code inside that repository is replaced.

**Alternatives considered.**

- *Patch v1 incrementally* — would have to reverse-engineer 20+ tangled defects in code I now disagree with. Cost of fixing > cost of rewriting.
- *Patch v2 (the more polished sibling)* — same issue, plus the architecture still rests on a single-sample inference design that the rebuild needs to abandon.
- *Start a separate `fall-guardian` repo on GitHub* — loses the "I built this in 2nd year and kept improving it" narrative that's actually valuable for portfolio storytelling.

**Consequences.**

- Old commit history is preserved (recoverable via `git log --all` or checkout of an older tag).
- The README, top-level structure, and language change entirely. Anyone visiting the repo today sees v3, not v1.
- Time investment is higher upfront but lower per-defect-fixed.

---

## ADR-002 — Sequential pipeline: edge prediction + cloud confirmation

**Status**: Accepted (2026-05-31)

**Context.** The edge ML question: should the on-device model do post-impact *detection* (alert after the user hits the ground) or pre-impact *prediction* (alert in the 300–500 ms window between fall initiation and ground impact)?

**Decision.** Both, sequentially. The edge model is a pre-impact predictor (firing fast, on-device, accepting some false positives). The cloud model is a post-impact detector running on the full window (slower, more accurate, suppresses the edge's false positives, assigns severity).

**Alternatives considered.**

- *Cloud-only detection* — simpler, fewer moving parts. Loses the edge-ML demo story (TinyML is hot in 2026) and is dependent on connectivity for any alerting. Rejected.
- *Edge-only detection* — extreme privacy story but very limited model capacity, more false positives. Rejected; loses the cloud verification that suppresses the noise.
- *Pre-impact prediction only* — strong demo ("the wearable beeps *before* the user hits the ground") but no second opinion. Single point of failure. Rejected.
- *Detection-first, prediction in v3.1* — safer, faster to a demo. Rejected because the prediction headline is the strongest part of the story and worth the extra training effort.

**Consequences.**

- Two models to train, evaluate, version, deploy. ~2× the ML work.
- Combined story is more compelling than either model alone — "we predict, we verify, we alert" maps cleanly to how real airbag-equipped fall systems work.
- Pipeline gives a clean failure mode if connectivity drops: the edge model still fires the haptic warning even when the cloud is unreachable; confirmation just happens later.

---

## ADR-003 — Wrist-worn form factor

**Status**: Accepted (2026-05-31)

**Context.** Fall-detection wearables in the literature live in several positions: waist (most accurate, used by KFall + SisFall + FARSEEING), thigh, chest (Phillips Lifeline lanyard form), or wrist (Apple Watch, Google Pixel Watch). Choice constrains both the dataset and the entire ML stack — wrist signals are qualitatively different from waist signals.

**Decision.** Wrist-worn, smartwatch-style.

**Alternatives considered.**

- *Waist / belt clip* — best signal quality, most academic data, but real users in 2026 don't wear belt-clip sensors voluntarily. "Best in the lab, never adopted in the field."
- *Pendant / chest lanyard* — matches the existing commercial market (Phillips Lifeline). Mid signal quality. Form factor is socially marked as "old person's panic button" — affects adoption.
- *Multi-position fusion* — best accuracy but bulky + harder to engineer. Out of scope for v3.

**Consequences.**

- Forces wrist-specific datasets (see ADR-006). Eliminates KFall + SisFall as training data — the original Phase 2 plan.
- Wrist-motion negatives (eating, waving, brushing teeth) become a major false-positive risk that has to be addressed explicitly in the Indian-ADL collection.
- Battery target is tighter (smartwatches need ≥24 h between charges; a belt-clip would let us run multi-day).

---

## ADR-004 — Edge-first hybrid inference (TFLite Micro on ESP32-S3)

**Status**: Accepted (2026-05-31)

**Context.** Where should ML inference live? The v1/v2 systems put everything in the cloud — the ESP32 just streamed raw IMU data. That's bandwidth-heavy, latency-sensitive to network conditions, and the model never gets to see the data unless the network is up.

**Decision.** Edge-first hybrid. The ESP32-S3 runs a TFLite Micro INT8 model continuously on the IMU stream. Only when the edge model fires (P(pre-impact) > threshold) does the device open a connection to the cloud and stream the triggering window for confirmation.

**Alternatives considered.**

- *Cloud-only inference* — simplest, but loses the edge-ML resume signal that's hot in 2026, requires constant connectivity, has cold-start risk on free-tier hosting.
- *Edge-only inference* — extreme privacy + zero network dependency, but limited model capacity = more false positives + no rich severity/contextual analysis. Rejected for v3.0; potential v3.x mode.
- *Hybrid with edge as filter, cloud as primary* — what we chose.

**Consequences.**

- Bandwidth in steady state = **zero** (raw IMU never leaves the device).
- Constraints on the edge model: ≤100 KB INT8, <80 ms inference. Forces a small ConvLSTM-tiny architecture; MicroNAS available if hand-design overshoots.
- Two models to maintain instead of one — but they share the same data pipeline and feature engineering for the cloud side.
- Privacy story is substantially better than v1/v2 (only suspected-fall windows reach the cloud, not 24/7 IMU streams).

---

## ADR-005 — Target user: elderly living alone, Indian context

**Status**: Accepted (2026-05-31). **The Indian-ADL *data-collection* requirement below is superseded by ADR-013** (per-user calibration); the Indian-context *targeting* still stands.

**Context.** Fall-detection products span several user groups: elderly at home (Phillips Lifeline, Apple Watch fall alert), post-op patients, construction workers, athletes. Each has a different ADL distribution that the model has to NOT misclassify as a fall.

**Decision.** Primary target = elderly living alone in an Indian household. ADL distribution explicitly includes Indian-specific activities (sukhasana / cross-legged sit, namaste / prayer poses, getting up from floor, squat-toilet posture).

**Alternatives considered.**

- *Elderly, Western-context* — train on existing datasets as published; defensible but the project loses its originality angle. Models trained only on Western ADLs misclassify sukhasana as a fall (subjects rapidly descend into floor-sitting).
- *General adults* — broader audience, less compelling "why this matters for India" story.
- *Multi-population transfer learning* — scope creep for v3.

**Consequences.**

- Need to collect a custom Indian-ADL supplement (target ~60–100 minutes, multiple subjects). This becomes a build milestone (Week E).
- Public-dataset training alone will under-represent these activities, so the Indian-ADL set is a non-negotiable input.
- Strong portfolio story: "trained on real Indian elderly motion patterns, not just Western lab data."

---

## ADR-006 — Wrist-only training datasets (WEDA-FALL primary; KFall + SisFall rejected)

**Status**: Accepted (2026-05-31). **Supersedes the dataset choice in Phase 2 (KFall + SisFall).**

**Context.** Phase 2 of the design locked KFall (pre-impact, elderly) + SisFall (post-impact, elderly) as the training datasets. Both are excellent — they're the standard references in the literature. **But both record at the waist or thigh, not the wrist.**

The wrist fall signature is qualitatively different from waist:

- The wrist is at the end of a long lever arm — rotational dynamics dominate.
- The arm-bracing reflex (extending the hand to absorb impact) produces a characteristic accel pattern that doesn't appear in waist data.
- Wrist ADLs have far higher motion noise than waist ADLs (eating, waving, brushing teeth) — the false-positive distribution is completely different.

Hoping that domain adaptation across body positions saves a wrist model trained on waist data is not a credible production approach. The cross-position transfer literature is thin and underwhelming.

**Decision.** Use only wrist-worn datasets for training: **WEDA-FALL** (primary), **SmartFall** (secondary, ADL augmentation), **UP-Fall** wrist channel (cross-dataset generalization test only). KFall and SisFall are kept as comparison references in the bibliography but never used as training data.

**Alternatives considered.**

- *Stay with KFall + SisFall and apply domain-adversarial training* — research-grade, not production-credible.
- *Stay with KFall + SisFall and physically remount sensors on the wrist for evaluation* — out of scope and confounds the published metrics.
- *Use UP-Fall as primary* — too small a fall set, only young subjects, 18 Hz is low.

**Consequences.**

- Lower-volume training data than waist datasets would have provided.
- The pre-impact label question is reopened — WEDA-FALL doesn't ship pre-impact labels the way KFall does. Addressed in ADR-007.
- Statistical power is reduced. Mitigation: combine WEDA-FALL + SmartFall + Indian-ADL for the cloud detection model. Document the trade-off honestly in the metrics report.
- The model trained on WEDA-FALL will generalize cleanly to other wrist devices in a way a waist-trained model would not.

---

## ADR-007 — Re-derive pre-impact labels from WEDA-FALL via peak-magnitude detection

**Status**: Accepted (2026-05-31)

**Context.** Pre-impact prediction (ADR-002) requires labelling the impact instant in each fall recording. KFall ships such labels; WEDA-FALL (our chosen primary, per ADR-006) ships a per-fall `(start_time, end_time)` covering the full 4-phase fall sequence but not the impact instant.

**Decision.** Re-derive the impact instant programmatically from WEDA-FALL's labelled fall windows by peak-magnitude detection:

1. Within `[start_time, end_time]`, compute `|a|(t) = sqrt(ax² + ay² + az²)`.
2. `t_impact = argmax_t |a|(t)` (constrained to the window).
3. Sanity check: peak `|a| ≥ 20 m/s²` (~2g).
4. Define `PRE_IMPACT = [t-500 ms, t-50 ms]` (clamped to `start_time`), `IMPACT = [t-50 ms, t+500 ms]`, `POST_IMPACT = [t+500 ms, end_time]`.
5. Validate against ground truth: report the `t_impact - start_time` lag distribution across all fall recordings.

**Alternatives considered.**

- *Hand-label the impact instant in each of the ~350 WEDA-FALL fall recordings* — slow, subjective, no audit trail.
- *Use the dataset's `start_time` as the impact moment directly* — wrong: `start_time` is the pre-fall onset (the dataset author confirms this corresponds to the start of the 4-phase sequence), not the impact peak.
- *Use a learned model for impact detection (e.g., train a separate impact-detection network)* — circular: would need labels to train the labeller.
- *Demote pre-impact prediction to a v3.1 stretch goal* — drops the strongest demo headline. Rejected.

**Consequences.**

- The re-derivation methodology becomes a documented part of the project — strong portfolio talking point ("I validated derived labels against the dataset's manual labels and report the disagreement statistics").
- The 20 m/s² threshold may exclude some genuine mild falls. Tunable.
- The lag-distribution histogram becomes a QA signal: outliers in that distribution are flagged for manual review (likely mislabelled recordings).
- The algorithm works identically on the Indian-ADL fall collection later, so we have one labelling pipeline for all wrist fall data.

---

## ADR-008 — Hardware-agnostic ingestion contract

**Status**: Accepted (2026-05-31)

**Context.** The real ESP32-S3 wearable lives at a friend's place. Development needs to proceed without the hardware on hand, but the system has to drop in cleanly when the hardware arrives.

**Decision.** Define a single JSON contract that the cloud `/v1/inference` endpoint accepts. Both the real ESP32 firmware and the Python virtual device emit this exact shape. The cloud has no knowledge of (and doesn't care about) which one is calling.

**Alternatives considered.**

- *Two separate endpoints, one for real device + one for virtual* — fragments the validation, doubles the test surface.
- *Skip the virtual device entirely, work with hardware only* — blocks all development progress until the hardware is in hand.
- *Use a different transport for the virtual device (e.g., file replay)* — loses the network-realism that's the whole point of the virtual device.

**Consequences.**

- The virtual device becomes a first-class component of the project — used for development, CI integration tests, and demos.
- When the hardware arrives, the only thing that needs to change is which client is running. Zero backend changes.
- The demo story is bulletproof: the recruiter can see the system working with the virtual device on a laptop, and trust that the hardware case behaves identically.

---

## ADR-009 — Monorepo structure

**Status**: Accepted (2026-05-30). **The `dashboard/` sub-project is dropped by ADR-014**; the monorepo decision itself stands.

**Context.** v3 spans ML training, Python backend, Flutter mobile, Next.js dashboard, ESP32 firmware, and a Python virtual device. Code organisation: monorepo or polyrepo?

**Decision.** Monorepo. Single GitHub repo with sub-projects: `ml/`, `backend/`, `mobile/`, `dashboard/`, `edge/`, `virtual_device/`, `docs/`. *(As built, the `dashboard/` sub-project was never created — see ADR-014.)*

**Alternatives considered.**

- *Polyrepo (one repo per sub-project)* — cleaner per-project ownership and CI, but the architecture story is hidden across 6 URLs. Recruiters reading a single GitHub link see less.
- *Two repos: `fall-guardian-core` (cloud + ML) and `fall-guardian-clients` (mobile + dashboard + edge)* — middle ground but doesn't actually help anything.

**Consequences.**

- Single CI/CD pipeline coordinated via GitHub Actions matrix.
- Single `git clone`, single README, single architecture story.
- Each sub-project has its own README + dependency manifest (`pyproject.toml`, `pubspec.yaml`, `package.json`).
- Common docs (`docs/`) covering the cross-cutting concerns.

---

## ADR-010 — uv as the Python package manager

**Status**: Accepted (2026-05-31)

**Context.** Python dependency management options in 2026: pip + venv (baseline), poetry (popular but slowing), pdm, uv (Astral, written in Rust). The ML sub-project needs reproducible installs, fast iteration on a laptop, and clean lockfile semantics.

**Decision.** uv. `pyproject.toml` declares the dependencies; `uv sync` produces a deterministic `uv.lock`. Standard tooling (`uv run`, `uv pip install`) wraps the existing Python ecosystem.

**Alternatives considered.**

- *pip + venv + requirements.txt* — works but no lockfile by default, slow installs.
- *Poetry* — full-featured but slower than uv and the 2024-era lockfile format had some issues; Astral's tooling is the 2026 default.

**Consequences.**

- Sub-second dependency resolution on installs.
- `uv run pytest` style commands for any tool that needs to be invoked through the environment.
- The user has to install `uv` once (`pip install uv` or `pipx install uv`). One-time setup cost.

---

## ADR-011 — Local grace period + dedicated retraining ingestion endpoint

**Status**: Accepted (2026-06-02)

**Context.** The edge model is recall-first and fires often (ADR-002, ADR-004): ~20% of ADL windows trip it. The cloud Transformer suppresses most, but the *user* is the ground truth for their own false alarms. We add a **local grace period**: on an edge trigger the watch buzzes for ~10 s; if the user presses Cancel, that 2.5 s window was a false alarm. Those canceled windows are the most valuable per-user fine-tuning / threshold-tuning data we can collect — but they must never trigger an alert or run through the detector. The cloud now ingests two semantically different kinds of windows (a live emergency vs. a confirmed-false-alarm training sample) and has to route them differently.

**Decision.** Expose the false-alarm upload on a **dedicated endpoint, `POST /v1/retraining`**, separate from `POST /v1/inference`. The retraining path skips the `CloudDetector` entirely and hands the window to a `RetrainingStore` that persists it labeled `CANCELED_FALSE_ALARM` (stubbed until MLOps persistence lands, mirroring the detector stub). Both endpoints validate against the same `WindowEnvelope` base model, so the locked 125-sample §8 contract (ADR-008) is enforced in exactly one place. A `payload_type` field (`emergency` | `retraining_data`) is added to the contract: it defaults to `emergency` on `/v1/inference` (so existing clients are unchanged) and is pinned to `retraining_data` on `/v1/retraining` (so a live trigger can't be diverted into the data-collection path).

**Alternatives considered.**

- *Single `/v1/inference` endpoint with a `payload_type` discriminator and a response union* — one ingestion URL, but overloads detection and data-collection on one route and forces `/v1/inference` to return two different response shapes. The dedicated endpoint keeps each route's response type singular and the concerns separated.
- *Reuse `/v1/inference` and special-case it server-side without a new route* — same overloading problem, and an emergency that should alert would share a code path with one that must be silently stored — a risky place for a bug.
- *Don't collect canceled windows at all* — throws away the highest-signal personalization data the product can get for free.

**Relationship to ADR-008.** ADR-008 rejected "two endpoints" *for the same concern* (real vs. virtual device emitting the **same** payload) because it fragments validation. This is different: detection and data-collection are **distinct concerns** with distinct responses, and they explicitly **share** the `WindowEnvelope` validator — so validation is not fragmented. ADR-008's hardware-agnostic contract still holds; `payload_type` is an additive, backward-compatible extension of it.

**Consequences.**

- The watch posts canceled windows to `/v1/retraining`; they are stored, never scored — no chance of a false-alarm upload paging a caregiver.
- `RetrainingStore` is the seam for future MLOps persistence (Postgres `retraining_samples`), gated on `FG_RETRAINING_DB_DSN`; swapping it in is a one-method change, zero API/schema impact — same philosophy as the `CloudDetector` stub.
- The §8 contract gains an optional `payload_type`; existing ESP32 firmware and the virtual device keep working without sending it.

---

## ADR-012 — Mobile live-feed transport: a hand-rolled `http` SSE consumer

**Status**: Accepted (2026-06-06)

**Context.** The caregiver app's headline feature is a real-time fall alert. The backend delivers confirmed falls over Server-Sent Events (`GET /v1/events/stream`, Phase 27): a per-user channel, `event: fall` JSON frames, a `: keepalive` comment every 15 s, and a `retry:` hint. On mobile the hard part isn't reading the stream — it's surviving a flaky network (reconnection, dead-socket detection, not hammering the server during an outage) plus the reality that an HTTP stream only lives while the app process does.

**Decision.** Consume the feed with a hand-rolled `FallEventService` over `package:http`'s `Client.send()`, owning all transport policy: an infinite reconnect loop, exponential backoff with jitter (cap 30 s), a 30 s idle **watchdog** keyed on the server's 15 s keepalive to catch half-open sockets, and a 401/403 short-circuit to an `unauthorized` state. State is Riverpod 3.x — a `StreamProvider` for connection status and a `NotifierProvider` that fans each event to both the in-app feed and an OS notification. Background / terminated delivery is explicitly out of scope for this layer: `flutter_foreground_task` (added) will keep the socket warm when backgrounded, and **FCM** is the terminated-state channel; both feed the same `FallEvent` sink.

**Alternatives considered.**

- *An SSE package (`eventsource` / `sse_channel`)* — less code up front, but they abstract away reconnection and give no hook for a keepalive-based watchdog; the dead-socket case (no `onDone`, no error, just silence) is what they handle worst. Owning ~150 lines buys the resilience the product needs.
- *WebSocket instead of SSE* — bidirectional and well-supported, but the backend already committed to SSE (one-way push, trivially proxyable, auto-`retry`); the client should match, not force a protocol change.
- *Polling `GET /v1/events`* — simple and survives backgrounding, but it's the exact latency-vs-load tradeoff Phase 27 was built to kill; a caregiver shouldn't learn about a fall on a 30 s poll.
- *FCM-only, no SSE* — the right terminated-state answer, but it makes the foreground experience depend on Google-infra round-trips and wouldn't show a sub-second live feed while the app is open. SSE-when-open + FCM-when-closed is the standard split.

**Consequences.**

- The UI is transport-agnostic: it consumes a `Stream<FallEvent>` + a `Stream<SseStatus>`, so adding the FCM producer later is additive — zero screen changes.
- Some resilience code (watchdog / backoff) is ours to maintain, but it's unit-testable and isolated to one file.
- A standing gap until the next slices: no JWT yet (the service idles `unauthorized` until the login flow lands) and no true background delivery (foreground / active only). Both are recorded in BUILD_LOG Phase 28's queue.

---

## ADR-013 — Drop the Indian-ADL dataset; personalise per-user instead (fit-at-first calibration)

**Status**: Accepted (2026-06-09, the mid-build audit). **Supersedes the Indian-ADL data-collection requirement in ADR-005.**

**Context.** ADR-005 made a custom Indian-ADL dataset the project's "originality angle": record ~60–100 minutes of Indian-specific activities (sukhasana, namaste, getting up from the floor, squat-toilet) so the model wouldn't misread them as falls. By the mid-build reckoning two things were true: (1) collecting, labelling, and validating a multi-subject dataset is a real sub-project competing with the firmware for the remaining time, and (2) a *better* mechanism already existed in the system — the per-user calibration seam (ADR-011, Phase 24) plus the canceled-false-alarm loop.

**Decision.** Drop the Indian-ADL collection. Replace it with **per-user fit-at-first calibration**: a ~10–15 min onboarding session where a new user simply wears the device, capturing *their own* ADL distribution (including whatever Indian-specific motions they personally do) as z-score normalisers + a threshold override applied at inference. The reserved synthetic-ADL engine is dropped for the same reason.

**Alternatives considered.**

- *Collect the dataset as planned* — strong "I collected original data" line, but a single averaged dataset is weaker than a model that fits each individual, and it costs time the firmware needs more.
- *Collect a smaller token dataset* — the worst of both: still a sub-project, still only an average.
- *Do both* — out of scope solo.

**Consequences.**

- The originality moves from "a dataset I collect once" to "a model that personalises to everyone" — a stronger product *and* a better story.
- FPR reduction on Indian-context motions now rides on calibration (ADR-011) + the 5-fold/SmartFall hardening (ADR-018), not on a bespoke corpus.
- `ml/DATA.md` §4 and `MODEL_CARD.md` §4.3 keep the Indian-ADL description but mark it superseded by this ADR; `DATA_LICENSES.md` §1.4's collection action items are retired.

---

## ADR-014 — Drop the Next.js caregiver web dashboard; the Flutter app is the sole caregiver client

**Status**: Accepted (2026-06-09, the mid-build audit). **Supersedes the `dashboard/` sub-project in ADR-009.**

**Context.** The locked design paired the Flutter app with a Next.js web dashboard (multi-device view, timeline, ack queue) consuming the same SSE feed. For an *emergency* alert, the right form factor is a phone in a pocket, not a browser tab — and the Flutter app already covers the caregiver completely.

**Decision.** Drop the web dashboard outright. The Flutter app is the only caregiver client. The gateway's `GET /v1/events/stream` stays transport-agnostic, so a web dashboard remains a clean future add-on (open the same SSE endpoint with a user JWT) without committing build time now.

**Alternatives considered.**

- *Build a minimal dashboard* — still a second UI to design, test, and keep in sync; the time belongs to the firmware (the actual differentiator).
- *Replace the app with a dashboard* — wrong form factor for a fall alert.

**Consequences.**

- No `dashboard/` directory is created; ADR-009's layout is updated to note this.
- `ARCHITECTURE.md` §2.5 and `PRIVACY.md` §13 (cookies) are updated to reflect "no web client."
- All caregiver features (live alerts, timeline, acknowledge, manual SOS) are delivered in the app.

---

## ADR-015 — Serve the cloud detector in-process as ONNX (no separate PyTorch inference service)

**Status**: Accepted (2026-06-02; export path hardened in Phase 30)

**Context.** The locked design (ARCHITECTURE §2.3 draft) imagined a separate, independently-scalable PyTorch inference service reached over an internal RPC. In practice the gateway is a single FastAPI process and the model is small.

**Decision.** Export the trained Transformer to **ONNX** and serve it **in-process** inside the gateway via `onnxruntime` (CPU provider); numpy does the preprocessing. The backend carries **no torch dependency**. The committed artifact (`backend/app/model/cloud_detector.onnx` + `.meta.json`) is loaded at startup by `CloudDetector`, which falls back to a transparent peak-acceleration stub if no artifact is present.

**Alternatives considered.**

- *Separate PyTorch RPC service* — independently scalable, but adds a network hop, a second container, and a torch runtime for a model that runs in milliseconds on CPU. Kept as a future upgrade path if load demands it.
- *TorchServe / Triton* — operational weight unjustified at this scale.

**Consequences.**

- One process, one deploy; the model is a diffable, version-pinned in-repo artifact (see ADR-018).
- The training stack stays PyTorch; ONNX is the serving boundary, so train/serve skew is limited to the documented preprocessing (shared `extract_features`).
- `MODEL_CARD.md` §1.1 and `ARCHITECTURE.md` §2.3/§4.1 updated from "FastAPI service on Fly.io, PyTorch" to "in-process ONNX (local)."

---

## ADR-016 — Additive alert routing: SSE for foreground, FCM for background/killed

**Status**: Accepted (2026-06-06, foreshadowed in ADR-012; backend side Phase 28b)

**Context.** The SSE feed (ADR-012, Phase 27) delivers sub-second alerts only while the app process is alive. A fall-detection app's whole point is reaching a caregiver whose phone is in their pocket, screen off — or whose app the OS has killed. FCM can wake a terminated app; SSE cannot. Running both naively would double every alert.

**Decision.** Use the two channels **additively, never redundantly**. On a confirmed fall (and manual SOS), `EventStore` publishes to the owner's per-user Redis channel for **SSE** *and* dispatches an **FCM** push to the registered token. The mobile client treats **SSE as the source of truth in the foreground and deliberately ignores foreground FCM messages**, so a fall yields exactly one alert. FCM covers only the background/killed states SSE can't. FCM is gated on `FG_FIREBASE_CREDENTIALS`; unset → SSE-only, no crash. The app registers its token via `PUT /v1/users/me/push-token`.

**Alternatives considered.**

- *FCM-only* — wakes a killed app but makes the foreground depend on Google round-trips and loses the sub-second live feed. Rejected (see ADR-012).
- *SSE-only* — clean foreground, but a pocketed/killed phone never hears the alert. Unacceptable for a safety product.
- *Both, ungated* — duplicate alerts; erodes trust. The foreground-FCM-suppression rule is the fix.

**Consequences.**

- A single fall produces a single caregiver alert regardless of app state.
- Both SSE and FCM fire even when the gateway is DB-less — an alert must reach a caregiver whether or not the row was persisted.
- The terminated-state gap called out at the end of BUILD_LOG Phase 28 is closed.

---

## ADR-017 — Local-first deployment via an ngrok tunnel (managed cloud deferred)

**Status**: Accepted (2026-06-13/15). **Supersedes the Fly.io / Supabase / Upstash / Vercel deployment in the original ARCHITECTURE §7.**

**Context.** The locked design deployed to Fly.io (gateway, `bom` region), Supabase (Postgres), Upstash (Redis), and Vercel (dashboard). Phase 32 first built the Fly.io path (`a05d0a7`), then it was removed (`56c934e`, `acb65dc`): for a single-operator portfolio system, a managed cloud added a monthly bill, account/secret management, and a deploy round-trip without changing what a reviewer sees.

**Decision.** Run **local-first**: the gateway on the host (`uvicorn … --port 8000`) with Postgres + Redis from the repo `docker-compose.yml`. Expose it to a **physical phone** through a **secure ngrok HTTPS tunnel** to port 8000 — a public TLS URL at **zero cost and zero added latency** (the tunnel just forwards to the host). The multi-stage `backend/Dockerfile` and the `FG_ENVIRONMENT=production` JWT-secret validator are kept as the seam for a future managed re-deploy.

**Alternatives considered.**

- *Keep Fly.io/Supabase* — a live URL is a nice portfolio line, but the cost/ops overhead isn't justified solo, and the cold-start sin it was meant to fix simply doesn't exist with no hosted instance.
- *LAN-IP only (`http://<ip>:8000`)* — works for the emulator and same-Wi-Fi phones but is plaintext and local-network-bound; ngrok's HTTPS also satisfies the FCM/TLS expectation the eventual cloud deploy would face, so the demo and production paths share a shape.

**Consequences.**

- $0/month; nothing leaves the laptop except FCM tokens (Google) and traffic transiting the ngrok tunnel.
- `ARCHITECTURE.md` §7, `RUN.md`, `PRIVACY.md` (§7.1/§8/§12), and the dir READMEs updated to local + ngrok; the Fly.io config is removed from the repo.
- A managed re-deploy remains one Dockerfile away — no re-architecture needed.

---

## ADR-018 — 5-fold cross-validated re-export + SmartFall hard negatives; preserve the baseline model

**Status**: Accepted (2026-06-11/14, Phase 30)

**Context.** Week C shipped the cloud detector honest about a 5.0% ADL FPR (target ≤2%), driven by impact-like ADLs (clapping, hit-table, jump). Two levers were identified to close it: a more stable decision threshold from full subject-stratified k-fold CV, and folding SmartFall's real-world activity in as **hard negatives**. Serious training needs a GPU not available on the dev laptop.

**Decision.** Adopt a **write-now-run-later** rhythm: author the 5-fold cross-validation wrapper (`ml/src/fall_guardian_ml/training/cross_validate.py`) and the cloud retrain locally, structured to run on Google Colab's GPU. Re-export the resulting model to ONNX (`ml/scripts/export_cloud_onnx.py`) into `backend/app/model/`, and **preserve the prior Phase-20 baseline verbatim** under `backend/app/model_old/` so it can be diffed, A/B-tested (`FG_MODEL_PATH`), or rolled back without git archaeology. Add `cascade_eval.py` and a `continuous_wear_sim.py` for the per-day alarm metric.

**Alternatives considered.**

- *Keep tuning on a single val split* — the cross-subject variance is exactly what inflates threshold uncertainty; single-split is what this fixes.
- *Overwrite the baseline in place* — loses the ability to compare against the pre-CV model; a model is the kind of artifact worth keeping a labelled previous version of.
- *MLflow-registry-only rollback* — works for training, but an in-repo `model/` ⇄ `model_old/` split gives a one-line serving rollback with no registry round-trip.

**Consequences.**

- The active served model is the 5-fold CV export; `model_old/` is the immutable baseline (see `MODEL_CARD.md` §3.2/§8, `backend/README.md`).
- The continuous-wear ≤0.5 alarms/day number is scripted; per `MODEL_CARD.md` §3.2 it is still owed a literal pass on a realistic activity mix (the per-window cascade FPR of 0.7% is on an adversarial impact-heavy set).
- INT8 TFLite export for the edge (`export_tflite.py` → `tflite_to_header.py`, validated by `validate_tflite.py`) is gated on a Linux toolchain (ADR notes in BUILD_LOG Phase 31).
