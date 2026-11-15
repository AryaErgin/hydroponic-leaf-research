# Hydroponic Leaf Research

Private research application measuring **visible projected leaf area** from photographs of lettuce and sorrel (*Rumex acetosa*), with mandatory human review and longitudinal growth history.

- Registered SegFormer segmentation and individual leaf proposals.
- Leaf count, individual areas, total, mean and median area.
- Physical calibration, resolution-aware reuse and calibration history.
- Touch-friendly Paint/Erase, Split/Merge, Add/Delete, Reassign and Undo/Redo.
- Human approval, growth graphs and CSV export.
- Firebase authentication, administrator approval and institution-isolated workspaces.

Stack: Python, FastAPI, PyTorch/SegFormer, NumPy/Pillow, HTML/CSS/JavaScript, Firebase Authentication, SQLite, Firestore/GCS adapters and Cloud Run. Local setup uses isolated emulation and SQLite, without production credentials.

## Quick start (Windows)

Install **64-bit Python 3.11**, **Node.js 22 or 24 with npm**, and Git. Then:

```powershell
git clone https://github.com/AryaErgin/hydroponic-leaf-research.git
cd hydroponic-leaf-research
.\setup.ps1
.\start.ps1
```

The browser opens at **http://localhost:8896**. Choose a reserved `.test` email and a unique password during setup. This creates your **local** owner; no real email is sent. One terminal manages Auth and the application. **Ctrl+C** stops only those services. Accounts, plants, calibrations, measurements and media survive restart in the ignored `.runtime/local/` directory. Existing data is never silently replaced.

### Real scientific mode: authorized model required once

The checkpoint has noncommercial research/evaluation restrictions and is **not in GitHub**. Obtain `research-model-exp006.zip` privately from an authorized maintainer. Put it in `.model-bundles/` before setup, or supply its path:

```powershell
.\setup.ps1 -ModelBundle 'C:\authorized-assets\research-model-exp006.zip'
```

Setup verifies the promoted checkpoint, registry, preprocessing, provenance, notices and SHA-256 against a checked-in allowlist, installs them and runs actual inference. No pretrained Hub cache, training dataset, production key or manual copying into model directories is needed. Missing assets stop **FULL** setup; they never produce a fake model or automatic classical fallback.

Setup prefers the qualified CUDA 12.8 Torch build with a compatible detected NVIDIA driver. For CPU-only use, run `.\setup.ps1 -Device cpu`; expect slower analysis. Drivers and system security settings are never changed.

For account/UI work without the checkpoint, explicitly use `.\setup.ps1 -Demo`. **DEMO is not a complete research installation:** learned inference is not verified, and automatic lettuce analysis can be unavailable. Previously installed models are retained; use a separate clone for a model-free demo.

## Check installation

```powershell
.\check.ps1
```

Checks cover Python/dependencies, Node, the real Auth emulator, SQLite integrity, built frontend, model hashes, actual selected-device inference and leaf split/area invariants. Browser editing/approval acceptance is separate. Read **How to use** on `/analyze` or `/help` for the professor's workflow.

See [Development](docs/DEVELOPMENT.md) for tests, rights, WSL/Linux, backups and troubleshooting. This setup does not deploy or migrate production.

## Deploy Your Own Cloud Instance

Use **your own dedicated Google Cloud project**, Google account and billing account. Install Python 3.11 x64, Git and [Google Cloud CLI](https://cloud.google.com/sdk/docs/install), then run:

```powershell
.\cloud-setup.ps1
```

The guided wizard checks the authorized model bundle, signs into a separate gcloud configuration, selects/creates your project with explicit consent, shows costs, provisions private storage and durable accounts, builds the real CPU model container, deploys Cloud Run, and guides your verified owner-account bootstrap. It never imports this project's production users or research data. Cloud Run defaults to **zero minimum / one maximum instance**. Billing and model-upload consent are required; budget alerts are not spending caps.

For a completely offline preview:

```powershell
.\cloud-setup.ps1 -PlanOnly -ProjectId your-own-research-project -ProjectNumber 123456789012 -Institution 'Your University'
```

**Validation status:** deployment tools, guards, dry runs and isolated Firestore namespaces are locally tested. No real cloud instance, container build, inbox delivery or GCS/redeployment acceptance has been verified for this new deployment path. See [Cloud deployment](docs/CLOUD_DEPLOYMENT.md) for prerequisites, manual Google/Firebase steps, costs, backups and the live acceptance checklist. Existing local setup remains separate.

## Scientific limits

Measurements describe visible projected lamina area, **not true 3D surface area**. Hidden/overlapping portions cannot be reconstructed from one photograph. Physical units require reviewed calibration and reference-plane assumptions. Proposals require human review; unreviewed masks are not ground truth. Successful inference establishes runtime functionality, not biological accuracy. Sorrel remains a separate, unvalidated learned-model target.

## Project structure

| Path | Purpose |
| --- | --- |
| `src/local_setup/` | Installer, supervisor, diagnostics and model validation |
| `src/firebase_preview/`, `src/auth_preview/` | Identities, accounts, roles and sessions |
| `src/integrated/` | Backend institution boundaries |
| `src/webapp/` | Calibration, scientific API, storage and browser editor |
| `src/hydroponic/`, `src/training/` | Measurement, inference and model registry |
| `configs/` | Nonsecret settings and model allowlist |
| `tests/`, `scripts/`, `docs/` | Checks, utilities and guides |

Generated data, annotations, experiments, results, weights and private runtime files are excluded.

## Security and rights

Keep this repository private. Never commit credentials, account exports, tokens, photographs, research data, weights or runtime artifacts. `.env.example` contains placeholders only. Organization authorization is enforced by the backend. Local checks do not qualify a cloud deployment.

No open-source license is granted. Code remains proprietary unless the owner authorizes a license. Dependencies retain their terms. The excluded SegFormer checkpoint has separate research/evaluation restrictions; source access grants no redistribution rights for weights or training data.
