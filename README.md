# Hydroponic Leaf Research

Private research application for reviewing hydroponic lettuce and kuzukulağı (*Rumex acetosa*) images, measuring **projected leaf area**, and tracking approved measurements over time.

The application supports a human-reviewed image-to-measurement workflow:

- Institution-scoped accounts, Firebase sign-in, email verification, roles, and administrator approval.
- Separate lettuce and kuzukulağı plant profiles and dated image records.
- Image upload/preparation and reviewed calibration for projected physical area.
- Classical CV and registered SegFormer candidate generation, subject to model availability and validation status.
- Manual mask and per-leaf editing with touch controls, zoom, and image navigation.
- Per-leaf area, total/mean/median leaf area, and leaf count for reviewed instances.
- Explicit human approval with preserved calibration, model, and measurement provenance.
- Measurement history, growth graphs, and CSV export of approved records.

Stack: **Python, FastAPI, HTML/CSS/JavaScript, NumPy/Pillow, Firebase Auth, SQLite, Firestore/GCS adapters, and optional PyTorch/SegFormer**. The production adapter was designed for Google Cloud Run; this source publication provides local development, not deployment credentials or a cloud release recipe.

## Run locally

The following Windows PowerShell setup runs the actual application with synthetic accounts and separate local data. See [Development](docs/DEVELOPMENT.md) for WSL/Linux commands, dependency scope, and optional Firestore tests. No cloud credentials, SMTP account, or production deployment are needed.

The repository contains application source and tests. Datasets, photographs, runtime databases, production configuration, and model checkpoints are excluded. **A fresh clone does not contain the promoted SegFormer model.** Default learned lettuce analysis therefore reports unavailable; it does not silently substitute another model. The existing explicitly selected classical method and manual review code remain available. See the development guide before attempting research-model setup.

1. Install **64-bit Python 3.11** and **Node.js 22 or 24 with npm**, then clone the private repository using an account with access:

   ```powershell
   git clone https://github.com/AryaErgin/hydroponic-leaf-research.git
   Set-Location hydroponic-leaf-research
   ```

2. Create a new virtual environment and install the web/test dependencies. This does not install Torch or download weights:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```

3. Install the pinned local Firebase tools and build the locked browser SDK:

   ```powershell
   npm --prefix .runtime/firebase-tools install --no-save --no-package-lock --no-audit --no-fund firebase-tools@15.22.4
   Push-Location src/firebase_preview/client
   npm ci --no-audit --no-fund
   .\node_modules\.bin\esbuild.cmd sdk-entry.js --bundle --minify --format=esm --platform=browser --outfile=../static/firebase-sdk.js
   Pop-Location
   ```

4. In terminal 1 at the repository root, start the loopback Auth emulator and leave it running:

   ```powershell
   $env:HYDRO_FIREBASE_TOOLS_ROOT = (Resolve-Path .runtime/firebase-tools/node_modules/firebase-tools).Path
   node scripts/run_firebase_auth_emulator.cjs
   ```

5. In terminal 2 at the repository root, bootstrap the first synthetic owner once, then start the server:

   ```powershell
   $env:FIREBASE_AUTH_EMULATOR_HOST = '127.0.0.1:9099'
   .\.venv\Scripts\python.exe scripts/bootstrap_demo_owner.py
   .\.venv\Scripts\python.exe -m uvicorn src.firebase_preview.run:build --factory --host 127.0.0.1 --port 8896 --no-access-log --no-proxy-headers
   ```

   Choose a password when prompted. The helper creates only `owner@example.test` / `demo-owner` and refuses to replace an existing account. Email verification is a local fixture, and no email is sent. Leave `K_SERVICE` unset; cloud execution is refused. `.env.example` documents shell variables; `.env` is not automatically loaded.

6. Open **http://localhost:8896/signin**, sign in as `owner@example.test` with your chosen password, and open `/analyze`. Read the [User guide](docs/USER_GUIDE.md), also available through **How to use** or at **http://localhost:8896/help**.

The app uses `.runtime/firebase/manual/`; Auth uses `127.0.0.1:9099`. Do not run competing instances on these ports. Java is unnecessary for this Auth/SQLite workflow; optional Firestore integration requires a Java 21 JDK. Local data and emulator identities are private fixtures, not production records.

## Check the source

These focused checks do not require datasets, checkpoints, or a running emulator:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts/build_user_guide.py --check
.\.venv\Scripts\python.exe -m pytest tests/test_demo_bootstrap.py tests/test_user_guide.py tests/test_integrated_app.py tests/test_integrated_science_workflow.py tests/test_firebase_durable_modes.py -q --tb=short --assert=plain
node tests/integrated_frontend_checks.cjs
node tests/firebase_access_checks.cjs
```

See [Development](docs/DEVELOPMENT.md#verification) for Auth/Firestore emulator suites and their requirements. Synthetic predictor tests verify application contracts; they do not establish actual model inference or biological accuracy. The full historical experiment suite requires separately authorized research assets.

## Scientific limits

- Measurements describe visible projected area, not true 3D leaf area or hidden tissue.
- Physical units require reviewed calibration and its geometric assumptions; pixel area alone does not establish cm².
- Candidate masks require human review and explicit approval. Ground-truth validation and plant-disjoint evaluation are required before claiming accuracy or growth performance.
- Lettuce and kuzukulağı have separate configuration and validation status. Kuzukulağı is not a validated learned-model target.

## Repository map

| Path | Purpose |
| --- | --- |
| `src/firebase_preview/` | Integrated Firebase identity frontend, session exchange, guarded local and production adapters |
| `src/integrated/` | Account and scientific-route integration with organization boundaries |
| `src/auth_preview/` | Account, role, approval, session, and repository services |
| `src/webapp/` | Scientific API, storage, calibration, review, and browser application |
| `src/hydroponic/`, `src/training/` | Classical measurement pipeline and research training/model-registry source |
| `configs/` | Nonsecret species/acquisition and scientific configuration |
| `tests/`, `scripts/` | Automated checks and local developer utilities |
| `docs/` | User and developer documentation |

## Security and rights

Keep the repository private. Never commit credentials, personal account exports, tokens, images, research data, trained weights, or runtime artifacts. Local emulator checks do not qualify a cloud deployment; preserve the existing authorization and namespace guards. The development guide explains which tests require emulators and which fixtures replace model inference.

No open-source license is granted by this repository. Project code remains proprietary unless the owner separately authorizes a license. Third-party dependencies retain their own terms. The excluded SegFormer-derived checkpoint has separate research/evaluation restrictions; possession of this source does not grant rights to redistribute it or its training data.
