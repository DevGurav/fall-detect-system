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

**Status**: Accepted (2026-05-31)

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

**Status**: Accepted (2026-05-30)

**Context.** v3 spans ML training, Python backend, Flutter mobile, Next.js dashboard, ESP32 firmware, and a Python virtual device. Code organisation: monorepo or polyrepo?

**Decision.** Monorepo. Single GitHub repo with sub-projects: `ml/`, `backend/`, `mobile/`, `dashboard/`, `edge/`, `virtual_device/`, `docs/`.

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
