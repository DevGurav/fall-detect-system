# Privacy Policy — Fall Guardian v3

This document explains how Fall Guardian handles personal data, the legal framework that applies to it, and the rights every user has. It is written to be readable by a non-lawyer first and to satisfy the requirements of India's **Digital Personal Data Protection Act, 2023 (DPDP Act)** second.

> **Scope.** Fall Guardian is a research / portfolio project authored by a single engineering student (Devendra Gurav, B.E. Artificial Intelligence & Data Science, Vidyavardhini College of Engineering & Technology, Mumbai University). It is not currently a commercial product. The privacy framework below describes how the system handles data **by design**, and the commitments that apply if the project is ever deployed beyond personal use.

> **DPDP applicability.** The DPDP Act 2023 applies to the processing of digital personal data within India, and to processing outside India that relates to offering goods or services to individuals in India. Fall Guardian's primary use case is elderly users in India, so the DPDP framework is the binding regulation.

---

## 1. Roles under the DPDP Act

| DPDP role | Mapped to Fall Guardian |
|---|---|
| **Data Principal** | The person whose data is being processed — i.e., the elderly user wearing the device, and the caregivers who receive alerts. |
| **Data Fiduciary** | The entity deciding the purposes and means of processing — i.e., the operator of a Fall Guardian deployment. For the current project this is the project author; for any third-party deployment, that operator becomes the Data Fiduciary. |
| **Data Processor** | Any third party processing data on behalf of the Data Fiduciary. **As built, the deployment is local-first** (see §7–§8): Postgres + Redis run in Docker on the operator's own machine, and the model artifacts live in-repo — so there is **no managed-cloud processor** for the system of record. The only third parties in the data path are **Firebase Cloud Messaging** (push tokens / notification titles), the **ngrok** tunnel (transits HTTPS traffic to the host; no storage), and optionally **Better Stack** (logs). Each is bound by its terms of service / DPA. |
| **Significant Data Fiduciary (SDF)** | Threshold-based designation by the Government. Fall Guardian's expected scale (personal / small-deployment) is well below SDF thresholds; if scale grows, SDF obligations (Data Protection Officer appointment, DPIA, independent audit) will apply. |

---

## 2. Data we collect

Fall Guardian distinguishes **three** categories. Each is collected only with explicit consent for a specified purpose.

### 2.1 Sensor data (the headline)

- **What**: 3-axis accelerometer, 3-axis gyroscope, orientation quaternion, battery level, signal-strength estimate. All from the wrist-worn device.
- **When sent off-device**: in steady state, **nothing is sent**. The edge model runs entirely on-device. Only when the edge model detects a possible fall is a short window (~2.5 s before + ~1 s after the trigger) uploaded to the cloud — either for confirmation (an emergency) or, if the user cancels it, as a labeled training sample (see below).
- **Purpose**: real-time fall safety monitoring, and — for windows the user explicitly cancels — improving the model's accuracy for that user. No other use is authorised.
- **Retention**: triggered windows are retained for **30 days** by default for model improvement; the user can opt out (in which case the cloud uses the window for inference and immediately deletes it).

**Canceled false alarms (the local grace period).** Because the edge model is recall-first and fires often, a triggered alert first buzzes the watch for ~10 s. If the user presses **Cancel**, the system treats that window as a false alarm: it is **not** routed to the detector and no caregiver is alerted. Instead the watch uploads the window to `/v1/retraining`, where it is stored labeled `CANCELED_FALSE_ALARM` and used to fine-tune the model / adjust that user's thresholds (the user is ground truth for their own non-falls). This is a user-initiated action, falls under the same "model improvement" purpose and the **30-day default retention + opt-out** above, and is presented separately in the consent flow (§3). The stored window is motion data only — never audio, video, or location.

### 2.2 Account data

- **What**: email address (auth identifier), display name, optional phone number (for SMS escalation), emergency contact details, caregiver list (other Fall Guardian users authorised to receive alerts for this device wearer).
- **Purpose**: account management, authentication, alert routing, escalation.
- **Retention**: held while the account is active. Deleted within 30 days of account-deletion request (DPDP Act §12).

### 2.3 Event + metadata

- **What**: fall events (timestamp, severity classification, confidence score, derived `t_impact` lead time), acknowledgement timestamps, device pairing events, audit log of administrative actions.
- **Purpose**: timeline display for caregivers, escalation logic, security audit trail.
- **Retention**: 5 years (in line with typical health-event retention norms); deleted on account-deletion request.

### 2.4 Data we deliberately do **not** collect

- Location data is not collected in steady state. GPS is acquired **only** when the user explicitly presses the in-app emergency button, and is sent only as part of the emergency SMS the user is initiating.
- Audio, photo, or video data is not collected.
- Health Connect / HealthKit data beyond what the user explicitly imports.
- Browsing data, third-party-app data, contacts, calendar, or any data unrelated to fall monitoring.

---

## 3. Lawful basis for processing (DPDP §4)

All processing rests on **specific, informed consent** captured at sign-up + during device pairing. The consent flow:

- Each data category in §2 is presented separately with its purpose, retention, and the consequence of refusal.
- Refusal of "account data" prevents account creation (no other path is available). Refusal of "sensor data collection beyond on-device" disables cloud confirmation but the on-device edge model still functions.
- Consent can be withdrawn at any time from the in-app **Privacy → Consent** screen. Withdrawal triggers immediate cessation of further collection in that category, and deletion within the deletion timeframes in §5.

For **emergency / vital-interests scenarios** (§7(c)), the SMS-with-GPS sent when the user presses the emergency button is processed even if the user has not consented to general location processing — because the user explicitly initiated the emergency request itself, which is the consent.

---

## 4. Notice (DPDP §5)

At every collection point we provide the information §5 requires:

- The personal data being collected
- The purpose for processing
- The manner in which the Data Principal can exercise their rights (§6)
- The manner in which a complaint can be filed with the Data Protection Board of India

Notices are presented in plain English **and Hindi** (per the bilingual scope of the project). Future work: Marathi (Mumbai University local language).

---

## 5. Data Principal rights (DPDP §11–14)

Every user can:

| Right | How to exercise it | SLA |
|---|---|---|
| **Access** — receive a summary of personal data being processed | In-app: Settings → Privacy → Export My Data | 30 days |
| **Correction** | In-app: Settings → Profile (account data); Privacy → Event History (event data) | Immediate for user-editable fields; 14 days for fields needing operator review |
| **Erasure** | In-app: Settings → Privacy → Delete Account | 30 days from request |
| **Grievance redressal** | Email the Grievance Officer (§9) | First response within 7 days; resolution within 30 days |
| **Nominate a digital nominee** | In-app: Settings → Privacy → Nominee | Effective immediately |
| **Withdraw consent** | In-app: Settings → Privacy → Consent per data category | Immediate; deletion follows §5 timelines |

---

## 6. Data minimisation + purpose limitation

The architecture is designed around minimisation, not as a policy on top of it:

- **Edge-first inference**: raw IMU never leaves the device in steady state (§2.1). The cloud sees only suspicious-event windows.
- **Per-purpose scoping**: every API endpoint is gated by JWT scope. The notifier service can read device tokens but not event payloads; the inference service can read events but not authentication state.
- **Database row-level security**: Postgres RLS policies enforce that an authenticated user can only read events scoped to their own `user_id` — even if the application code has a bug.
- **No analytics tracking SDKs**. We do not embed Google Analytics, Mixpanel, Segment, or any third-party telemetry that could profile users.

---

## 7. Storage + security

### 7.1 Where the data lives

**As built, the system is local-first** — the system of record runs on the operator's own machine, not a managed cloud.

| Data category | Location | Provider | Encryption at rest | In transit |
|---|---|---|---|---|
| Account, devices, events, calibration, retraining windows, audit | PostgreSQL 16 in local Docker (on the operator's machine) | self-hosted | OS/disk-level (operator-managed) | TLS via the ngrok tunnel |
| FCM push tokens | `users.push_token` column in the same local Postgres (migration 0004) | self-hosted | OS/disk-level | TLS |
| Rate-limit counters + SSE pub/sub | Redis 7 in local Docker (ephemeral; no PII) | self-hosted | n/a (in-memory) | localhost |
| Model artifacts | committed in-repo (`backend/app/model/`, `model_old/`) — no personal data | — | n/a | n/a |
| Application logs (optional) | stdout; optionally Better Stack drain | Better Stack | provider-managed | TLS 1.3 |

There is **no object storage** (triggered windows are stored as rows in Postgres
`retraining_samples`, not in a cloud bucket) and **no Firestore** (FCM tokens live
in the local Postgres). The ngrok tunnel terminates TLS and forwards to the host;
it stores nothing.

### 7.2 Security baseline

Per `docs/ARCHITECTURE.md` §5:

- OAuth 2.0 + JWT with refresh-token rotation
- Per-device JWTs stored in ESP32-S3 encrypted NVS partition
- HTTPS + HSTS + cert pinning on the wearable
- 8-character Crockford-base32 pairing codes with 5-minute TTL + exponential backoff on attempt failure
- Pydantic schemas on every endpoint
- Postgres row-level security
- `audit_events` table logging every administrative action
- Secrets (JWT signing key, Firebase service-account JSON) kept in a **gitignored `backend/.env`**, never committed; `FG_ENVIRONMENT=production` refuses to boot with the dev JWT secret

### 7.3 Incident response

Suspected or confirmed data breach is reported to the Data Protection Board of India within **72 hours** (DPDP §8(6)) and to affected Data Principals without undue delay. Specific procedure in `docs/SECURITY.md` (to be added).

---

## 8. Cross-border data transfer (DPDP §16)

The DPDP Act permits transfer of personal data outside India to any country **except those specifically restricted** by the Central Government (no restrictions notified as of the date of this document — verify at deployment time).

**As built, the local-first deployment minimises cross-border transfer to near-zero.** The system of record (Postgres) and all triggered windows stay on the operator's own machine; nothing is transferred to a managed-cloud database or object store. The only data that leaves the operator's machine:

- **Firebase FCM**: Google's (US-based) service. Only the **push token** and the **notification title/body** ("Fall Detected — severity, device id") cross the border — never the IMU window, the event payload, or account data.
- **ngrok tunnel**: forwards HTTPS traffic between the phone and the host. Traffic transits ngrok's edge; it is not stored. Operators preferring no third-party transit can use the same-Wi-Fi LAN-IP path instead (see `RUN.md`).
- **Better Stack** (optional log drain): only if `FG_BETTER_STACK_TOKEN` is set.

The original managed-cloud plan (Supabase/Fly.io in the `ap-south-1`/`bom` Mumbai region, Cloudflare R2) was dropped in favour of this local-first deployment (ADR-017). A future managed re-deploy should restore the India-region preference and disclose operating regions at sign-up.

---

## 9. Grievance Officer (DPDP §8(9))

The operator of any Fall Guardian deployment must appoint a Grievance Officer. For the project-author deployment:

- **Name**: Devendra Gurav
- **Email**: `prasad.gurav09@gmail.com`
- **Response SLA**: first response within 7 days; resolution within 30 days

If unsatisfied with the Grievance Officer's response, the Data Principal may approach the **Data Protection Board of India** (per DPDP §27 once the Board is fully operational).

---

## 10. Children's data (DPDP §9)

Fall Guardian's primary user is elderly; the product is **not designed for children under 18**. If a child user account is identified, processing of that account requires verifiable parental consent or the account is suspended pending such consent.

---

## 11. Automated decision-making + AI transparency

Fall Guardian uses machine-learning models to predict and detect falls. Each model's design, training data, and known limitations are documented in `docs/MODEL_CARD.md`. The system never makes legally-binding or denial-of-service decisions automatically — the only automated action is alerting a caregiver, which the user has consented to.

The user (or their caregiver) can dispute an automated decision by contacting the Grievance Officer (§9). Disputes that surface model failure modes are escalated to a model card update and (if warranted) a retraining cycle.

---

## 12. Third-party services + Data Processors

As built (local-first), the third-party surface is small:

| Service | Purpose | Data shared |
|---|---|---|
| **Firebase (FCM)** | Push to a backgrounded/killed app | Device FCM token; notification title/body (severity + device id); no IMU window, no event payload, no account data |
| **ngrok** | HTTPS tunnel from a physical phone to the local host | Transits request/response traffic to `:8000`; stores nothing |
| **Better Stack** | Application logs (optional, off by default) | Structured JSON logs (no raw IMU, no PII beyond error context) — only if `FG_BETTER_STACK_TOKEN` is set |
| **Supabase / Fly.io / Cloudflare R2 / Sentry** | **Not used** — dropped with the managed-cloud plan (ADR-017). Listed to confirm. | — |
| **OpenAI / Anthropic** | **Not used** in Fall Guardian. Listed explicitly to confirm. | — |

Each provider's DPA / privacy commitments are reviewed before use. Switching providers (or adding one in a future managed re-deploy) triggers a notice to all Data Principals.

---

## 13. Cookies + local storage

There is **no web client** — the caregiver-facing surface is the Flutter mobile app
(the planned Next.js web dashboard was dropped, ADR-014), so Fall Guardian sets **no
cookies** of any kind. On the device, the app uses platform **secure storage**
(Keychain / Keystore) to hold the user's JWTs and the per-device token; this is
essential to authentication and is never used for tracking. **No** third-party
tracking, analytics, or advertising SDKs are embedded.

---

## 14. Changes to this policy

Material changes (new data categories, new processors, broader purposes) are notified to all active users via in-app banner + email at least 14 days before taking effect. Non-material changes (clarifications, formatting, legal-framework updates) are published in the repository's `git log` and visible at `docs/PRIVACY.md`.

The version + last-updated date of this policy is shown in-app on the **Settings → About** screen and at the bottom of this document.

---

## 15. Contact

- Project author + Grievance Officer: **Devendra Gurav**, `prasad.gurav09@gmail.com`
- Public repository: `github.com/DevGurav/fall-detect-system`
- For Indian regulatory escalation: **Data Protection Board of India** (once operational)

---

*Privacy Policy — drafted 2026-05-31 alongside the v3 rebuild; updated for the as-built **local-first** deployment (Docker Postgres/Redis on the operator's machine + an ngrok tunnel; FCM tokens in local Postgres; no managed-cloud processor; no web client). See ADR-014 (dashboard dropped) and ADR-017 (local deployment). To be revisited if the project is re-deployed to a managed cloud.*

*Legal note: this document is drafted to satisfy the DPDP Act 2023 framework based on the project author's understanding of the Act and the Draft DPDP Rules. It is not legal advice and should be reviewed by a qualified legal practitioner before any commercial deployment.*
