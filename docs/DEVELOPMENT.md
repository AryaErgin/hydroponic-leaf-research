# Local development

This workflow runs the existing integrated application, frontend, and user guide with a loopback Firebase Auth emulator and local SQLite/media. It does not deploy, authenticate to Google Cloud, import production accounts, or retrieve scientific data or weights.

## Prerequisites and scope

- Use **64-bit CPython 3.11**. The existing qualified Windows runtime is 3.11.9. Newer Python versions are not qualified by this guide.
- Use Node.js 22 or 24 with npm. The browser SDK is locked in `src/firebase_preview/client/package-lock.json`; the Auth wrapper requires exactly `firebase-tools@15.22.4`. Do not silently upgrade it because the wrapper imports CLI internals.
- A Java 21 JDK is required only for the optional Firestore emulator. Auth-only development does not need Java.
- First installation needs network access to Python/npm package registries, and Firestore needs its official emulator download. Subsequent local use needs the installed packages and generated SDK bundle.
- Run from the repository root. Use separate environments for Windows and WSL; do not share a venv or `node_modules` across platforms. Put project files and generated outputs under this repository.

The local origin is fixed to `http://localhost:8896`; bind Uvicorn to loopback and use that URL consistently. Auth is fixed to `127.0.0.1:9099`; optional Firestore tests use `127.0.0.1:8791`. Stop another local instance before reusing its ports. The app uses `.runtime/firebase/manual/{auth,science}`; every clone has its own runtime. Avoid starting two servers against that same directory.

`.env.example` is a reference template. **No dotenv loader reads `.env` automatically.** Set environment variables explicitly in every terminal that needs them. The local entry point constructs its own storage settings: production `HYDRO_DATA_DIR`/Firestore variables do not configure this local app. Do not use `src.firebase_preview.production:build`, `src.firebase_preview.live_test`, or cloud container recipes for this workflow.

## Windows PowerShell

Clone the private repository using an account with access, then enter it:

```powershell
git clone https://github.com/AryaErgin/hydroponic-leaf-research.git
Set-Location hydroponic-leaf-research
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm --prefix .runtime/firebase-tools install --no-save --no-package-lock --no-audit --no-fund firebase-tools@15.22.4
Push-Location src/firebase_preview/client
npm ci --no-audit --no-fund
.\node_modules\.bin\esbuild.cmd sdk-entry.js --bundle --minify --format=esm --platform=browser --outfile=../static/firebase-sdk.js
Pop-Location
.\.venv\Scripts\python.exe scripts/build_user_guide.py --check
```

No venv activation is required. Keep the following Auth emulator command running in terminal 1 at the repository root. The wrapper disables provider action-link logging and does not send emails:

```powershell
$env:HYDRO_FIREBASE_TOOLS_ROOT = (Resolve-Path .runtime/firebase-tools/node_modules/firebase-tools).Path
node scripts/run_firebase_auth_emulator.cjs
```

In terminal 2, create the first **synthetic** local owner once, then start the app:

```powershell
$env:FIREBASE_AUTH_EMULATOR_HOST = '127.0.0.1:9099'
.\.venv\Scripts\python.exe scripts/bootstrap_demo_owner.py
.\.venv\Scripts\python.exe -m uvicorn src.firebase_preview.run:build --factory --host 127.0.0.1 --port 8896 --no-access-log --no-proxy-headers
```

The bootstrap prompts for a password of 15 to 128 characters, matching the existing sign-in form. It creates only `owner@example.test` (`demo-owner`), dynamically binds its new emulator UID, and grants it a local owner role. It simulates verified-email proof in the emulator; that is not real email delivery or verification. It refuses an existing owner or existing demo account and never resets credentials. For unattended synthetic acceptance only, `HYDRO_DEMO_PASSWORD` supplies the same password input; remove the variable afterward with `Remove-Item Env:HYDRO_DEMO_PASSWORD`. Do not store it in a tracked file or print provider responses.

Open **http://localhost:8896/signin**, sign in with `owner@example.test` and the password you chose, then open `/analyze` or `/help`. The local organizations are fixtures; no institution affiliation is asserted. Further registrations remain pending until an administrator approves them and emulator email proof is completed. Use the owner UI to review membership requests. The guarded bootstrap is the simplest first-account path; `src.auth_preview.manage` provisions a different legacy credential system and is not a Firebase owner bootstrap.

## WSL/Linux equivalent

Install Python 3.11 and its venv support using your distribution's tooling, then clone inside a project directory accessible to that environment. Use the following commands from the repository root:

```bash
python3.11 -m venv .venv-wsl
.venv-wsl/bin/python -m pip install -r requirements-dev.txt
npm --prefix .runtime/firebase-tools install --no-save --no-package-lock --no-audit --no-fund firebase-tools@15.22.4
(cd src/firebase_preview/client && npm ci --no-audit --no-fund && ./node_modules/.bin/esbuild sdk-entry.js --bundle --minify --format=esm --platform=browser --outfile=../static/firebase-sdk.js)
.venv-wsl/bin/python scripts/build_user_guide.py --check
export HYDRO_FIREBASE_TOOLS_ROOT="$PWD/.runtime/firebase-tools/node_modules/firebase-tools"
node scripts/run_firebase_auth_emulator.cjs
```

In a second shell at the same repository:

```bash
export FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
.venv-wsl/bin/python scripts/bootstrap_demo_owner.py
.venv-wsl/bin/python -m uvicorn src.firebase_preview.run:build --factory --host 127.0.0.1 --port 8896 --no-access-log --no-proxy-headers
```

Use `unset HYDRO_DEMO_PASSWORD` after unattended fixture creation. Windows and WSL share host ports under common networking configurations; do not start competing emulators. These commands are platform equivalents; consult the publication report for which operating systems were actually exercised in this release.

## Dependencies and model availability

`requirements-dev.txt` includes `requirements-firebase-preview.txt`, `requirements-web-test.txt`, and therefore `requirements-web.txt`, plus pinned pytest and requests. It pins Pydantic 2.13.5 to match the qualified web runtime and native core instead of resolving a newer unqualified binary. The runtime uses FastAPI, Starlette, Uvicorn, python-multipart, SQLAlchemy, NumPy, Pillow/pillow-heif, Firebase Admin, and Google storage libraries. The web requirements retain the existing PostgreSQL driver and cloud adapters even though this loopback path uses SQLite/files. Tests use pytest, requests, httpx, and httpx2; both HTTP clients are required by the existing test environment.

The web and classical CV import paths do **not** require SciPy, OpenCV, or Torch. Learned-model runtime imports are lazy: `src/training/models.py` and `src/training/inference.py` load Torch only when explicitly used. Training/inference requires an independently compatible PyTorch/torchvision installation, safetensors, and Transformers for SegFormer, plus authorized local model artifacts. These are intentionally outside the web setup. No generic `pip install torch` command establishes compatibility or recreates the excluded experiment.

`models/`, datasets, derived checkpoints, registry/promotion files, and pretrained weights are not distributed. With an empty registry, `/api/models` reports no champion. Default `auto` lettuce analysis raises an unavailable-model error and generates no substitute mask. Selecting the existing **classical** engine explicitly runs its unvalidated candidate path; this is not a replacement validation or SegFormer reproduction. Manual review and scientific approval rules still apply. Kuzukulağı has separate limitations and no validated learned model.

To restore research inference, an authorized maintainer must separately supply the complete immutable registry version (checkpoint, config, provenance, evaluation, training/validation manifests as applicable), verify recorded hashes and rights, and use the existing reviewed promotion workflow. Do not create a fake champion or relabel an untrained model. The SegFormer lineage has NVIDIA research/evaluation restrictions; the source publication grants no redistribution permission for weights or data.

## Verification

Run these checks with the installed development environment. On WSL replace `.\.venv\Scripts\python.exe` with `.venv-wsl/bin/python` and use the same Python arguments.

Without emulators, run the developer guards, guide packaging, account/scientific integration, and synthetic scientific workflow:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts/build_user_guide.py --check
.\.venv\Scripts\python.exe -m pytest tests/test_demo_bootstrap.py tests/test_user_guide.py tests/test_integrated_app.py tests/test_integrated_science_workflow.py tests/test_firebase_durable_modes.py -q --tb=short --assert=plain
node tests/integrated_frontend_checks.cjs
node tests/firebase_access_checks.cjs
node tests/firebase_production_ui_checks.cjs
```

With the loopback Auth emulator running:

```powershell
$env:FIREBASE_AUTH_EMULATOR_HOST = '127.0.0.1:9099'
.\.venv\Scripts\python.exe -m pytest tests/test_firebase_identity.py tests/test_firebase_email_actions.py -q --tb=short --assert=plain
node tests/firebase_sdk_checks.cjs
```

`--assert=plain` avoids assertion introspection printing emulator tokens/action codes. These tests create synthetic accounts and action codes, never real mail. Some synthetic workflows inject predictors or model records: their passing status establishes HTTP/storage/approval contracts, not actual SegFormer inference or biological accuracy.

### Optional durable Firestore integration

This section tests persistence adapters against **local emulators**, not the running SQLite developer app. Install a Java 21 JDK and confirm `java -version` in the terminal launching Firestore. Keep Auth running. Create a local ignored CLI configuration and start Firestore in another terminal:

```powershell
New-Item -ItemType Directory -Force .runtime | Out-Null
'{"emulators":{"firestore":{"host":"127.0.0.1","port":8791},"ui":{"enabled":false},"singleProjectMode":false}}' | Set-Content -Encoding ascii .runtime/firebase-emulators.json
$env:FIREBASE_EMULATORS_PATH = Join-Path (Get-Location) '.runtime/firebase-emulator-cache'
node .runtime/firebase-tools/node_modules/firebase-tools/lib/bin/firebase.js emulators:start --only firestore --project demo-hydroponic-leaf-research --config .runtime/firebase-emulators.json
```

For WSL, write the same JSON to `.runtime/firebase-emulators.json`, set `export FIREBASE_EMULATORS_PATH="$PWD/.runtime/firebase-emulator-cache"`, and run the same `node` command. Do not use `firebase login` or select a real cloud project. Existing production project identifiers are retained in the guarded adapter source; with these tests, requests are forced to the exact loopback emulators, and fixture namespaces are unique.

Run the focused persistence suite in the Python terminal:

```powershell
$env:FIREBASE_AUTH_EMULATOR_HOST = '127.0.0.1:9099'
$env:FIRESTORE_EMULATOR_HOST = '127.0.0.1:8791'
.\.venv\Scripts\python.exe -m pytest tests/test_firebase_repository.py tests/test_firebase_durable_workflow.py tests/test_firebase_restore.py -q --tb=short --assert=plain
```

Some tests skip if required emulators are missing. A skipped check is not a passing durable integration test. The full historical suite also contains data-, model-, display-, and platform-dependent experiments; `pytest tests` is not a fresh-clone acceptance command. Retain the test selection and explicit limitations in a release report.

## Data, configuration, and release boundaries

Local auth metadata is `.runtime/firebase/manual/auth/auth.sqlite3`; organization-scoped scientific databases and media are under `.runtime/firebase/manual/science/`. The runtime is private and ignored. Auth emulator accounts are in memory unless explicitly exported by an operator. Restarting Auth without restoring its matching identities invalidates existing local app sessions; the bootstrap deliberately does not overwrite an existing backend owner. For a new exercise use a separate fresh clone/runtime with a fresh emulator, rather than copying production or deleting another research workspace.

The repository excludes `.env`, service-account material, Firebase web configuration, runtime databases, datasets, model binaries, experiment outputs, browser SDK builds, `node_modules`, and virtual environments. `.gitignore` reduces accidental staging; it does not sanitize already tracked files. Review the actual staged tree before publication.

The retained cloud adapters describe an existing separately configured deployment. Historical Dockerfiles are excluded from this source publication because they depend on a private scientific base image containing excluded weights; there is no standalone fresh-clone container build. No credentials, production configuration, model download, deployment, or account migration is part of local setup. Do not weaken origin, emulator, institution, email-verification, session, or approval checks to bypass missing configuration.

There is no project open-source license. Keep this repository private unless the owner separately authorizes publication and licensing. Dependency licenses and excluded research-asset terms remain independent.

Nonsecret project identifiers and origins are retained in explicit production guards and mocked configuration tests; they are not credentials and the local emulator path does not contact them. Optional authorized cloud utilities require the Google Cloud CLI on PATH; no workstation-specific path is distributed.
