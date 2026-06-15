# Data Licenses

Tracking sheet for the licence terms + attribution requirements of every dataset Fall Guardian uses (or considers and rejects). Maintained because (1) academic datasets routinely have non-trivial licence restrictions, (2) the project must comply with each before training, and (3) recruiters reading a health-tech ML project value seeing the licence diligence has been done.

> **How to use this file.** Before adding any new dataset to the pipeline, append a row to §1 with the licence verified by reading the upstream `LICENSE` file (not just a third-party summary). Before publishing model artefacts trained on a dataset, confirm the licence permits redistribution / derived works.

---

## 1. Datasets in use

### 1.1 WEDA-FALL — primary training corpus

| Field | Value |
|---|---|
| Source repository | `https://github.com/joaojtmarques/WEDA-FALL` |
| Reference paper | Marques, J. et al. (2024). *Wrist-Based Fall Detection: Towards Generalization across Datasets*. Sensors 24(5), 1679. <https://www.mdpi.com/1424-8220/24/5/1679> |
| Author contact | `joaojtmarques@tecnico.ulisboa.pt` |
| Licence | **To verify against the upstream `LICENSE` file in the repo.** The README does not include explicit licence text. The dataset is shared publicly via GitHub for academic + research use. Before any commercial use or model redistribution, email the author for written confirmation. |
| Attribution required | **Yes** — cite the Sensors paper above + link the GitHub repo. |
| Redistribution of derivatives | Permissible for trained model weights (no raw data redistribution) under typical academic dataset norms; verify before tagging a v3.x release. |
| Local copy location | `ml/data/raw/WEDA-FALL-main/` (gitignored) |
| Last licence check | 2026-05-31 (initial download) |

**Action item**: open an issue / email the author to obtain a written licence statement before the v3.0.0 release tag.

---

### 1.2 SmartFall — secondary corpus (ADL augmentation)

| Field | Value |
|---|---|
| Source | Texas State University, Department of Computer Science (Anne H. Ngu's lab) |
| Reference paper | Mauldin, T. F., Canby, M., Metsis, V., Ngu, A. H. H., & Rivera, C. C. (2018). *SmartFall: A Smartwatch-Based Fall Detection System Using Deep Learning*. Sensors 18(10), 3363. <https://www.mdpi.com/1424-8220/18/10/3363> |
| Access | Available on academic request via Anne Ngu's lab page or by emailing the author (`a.ngu@txstate.edu` — verify current address before contact) |
| Licence | Academic research use as granted by the lab. **Specific terms to be confirmed in the access agreement signed at download time.** |
| Attribution required | **Yes** — cite the Sensors paper above. |
| Redistribution of derivatives | Likely permitted for academic publications; restricted for commercial use. **Confirm in the access agreement.** |
| Local copy location | `ml/data/raw/smartfall/` (to download — gitignored) |
| Last licence check | Not yet acquired — pending email to author |

**Action item**: complete the access request before Week C (cloud-model training).

---

### 1.3 UP-Fall — cross-dataset generalization corpus

| Field | Value |
|---|---|
| Source | Universidad Panamericana — `https://sites.google.com/up.edu.mx/har-up/` |
| Reference paper | Martínez-Villaseñor, L., Ponce, H., Brieva, J., Moya-Albor, E., Núñez-Martínez, J., & Peñafort-Asturiano, C. (2019). *UP-Fall Detection Dataset: A Multimodal Approach*. Sensors 19(9), 1988. <https://pmc.ncbi.nlm.nih.gov/articles/PMC6539235/> |
| Access | Public download from the HAR-UP site after the registration form |
| Licence | Open academic use; **specific terms to be confirmed from the dataset's licence file at download.** |
| Attribution required | **Yes** — cite the Sensors paper. |
| Use scope in this project | Cross-dataset evaluation only (wrist channel). Not used for primary training. |
| Local copy location | `ml/data/raw/upfall/` (to download — gitignored) |
| Last licence check | Not yet acquired — pending Week C |

**Action item**: download + read the licence statement when acquiring; update this row.

---

### 1.4 Indian-ADL — collected by the project — DROPPED (ADR-013)

> **Not collected.** The Indian-ADL collection was **dropped at the mid-build audit**
> in favour of per-user fit-at-first calibration (ADR-013): the system captures each
> user's own ADL distribution at onboarding instead of training on one collected
> corpus. No subjects were recorded, so the licence/consent action items below did
> not execute. The row is retained for provenance and in case collection is ever
> revived. The licence intent (CC BY 4.0) would still apply if it were.

| Field | Value |
|---|---|
| Source | Original collection by Devendra Gurav (project author) + consented family/friend subjects |
| Reference | n/a (first release of this collection) |
| Licence | **CC BY 4.0** (Creative Commons Attribution 4.0 International) — free use with attribution. This licence is consistent with the wrist-based fall literature's openness norms and signals the data is for the research community. |
| Attribution required | Yes — attribution string: *"Indian-ADL supplement (2026), collected as part of Fall Guardian v3 by Devendra Gurav. github.com/DevGurav/fall-detect-system"* |
| Subject consent | Required per `docs/PRIVACY.md`. Each subject signs a one-page consent form (template in `ml/data/raw/indian_adl/CONSENT_FORM.pdf` once collection starts) authorising the collection, the CC BY 4.0 publication, and the right to withdraw before publication. |
| Subject anonymisation | All subject IDs are abstract integers; no name, no contact info, no identifying metadata stored alongside the IMU CSVs. |
| Local copy location | `ml/data/raw/indian_adl/` (gitignored during collection; publication path TBD) |
| Last licence check | n/a (this project decides the licence) |

**Action item**: write the consent-form template + the subject-tracking ledger (kept offline, not in git) before collection begins in Week E.

---

## 2. Datasets considered + rejected

For transparency / so we don't accidentally re-evaluate the same datasets:

| Dataset | Why rejected | Licence (for reference) |
|---|---|---|
| **KFall** | Waist + thigh sensor placement; sensor-position transfer to wrist is not credible. See `DECISIONS.md` ADR-006. | CC BY 4.0 (per Frontiers OA policy) |
| **SisFall** | Waist sensor placement; same reason as KFall. | Free for academic research, request via SISTEMIC site |
| **UMA-Fall** | Smaller / less diverse wrist subset than alternatives. | CC BY 4.0 |
| **FARSEEING** | Lower-back placement; access is restricted (member-only). | Member-restricted academic licence |
| **MobiAct** | Smartphone (pocket) placement; not wrist. | Free for academic research |

---

## 3. Licence-compliance checklist (per release)

Before tagging any public release (e.g., `v3.0.0`):

- [ ] All datasets in §1 have a verified licence statement (not "to verify")
- [ ] Each citation appears in the project README and the relevant model-card section
- [ ] Trained model artefacts ready for redistribution have licence-compatibility cleared
- [ ] If any dataset prohibits derivative-work redistribution, the affected models are clearly marked as "not for redistribution"
- [x] Indian-ADL consent forms — **N/A**: collection dropped (ADR-013), no subjects recorded
- [ ] No raw dataset files committed to the repo (`.gitignore` enforces this)
- [ ] BibTeX entries in `ml/DATA.md` reflect the canonical citations

---

## 4. Maintenance

This file is updated whenever:

- A new dataset enters the pipeline
- An existing dataset's licence terms change
- A licence-compliance check fails
- A redistribution decision needs to be documented for audit

Update happens in its own atomic commit (`docs: update DATA_LICENSES for <dataset>`).

---

*DATA_LICENSES v0 — drafted 2026-05-31. Verify each "to verify" row before the first public release tag.*
