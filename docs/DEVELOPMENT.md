# Development and scientific runtime

## Local setup and persistence

Use the four commands in [README](../README.md). Required: Python 3.11 x64 and Node 22/24 with npm. Setup pins the web dependencies, model runtime and `firebase-tools@15.22.4`, and builds the locked browser SDK. Auth-only emulation uses no Java. No Google login, production credentials, SMTP, Firestore or GCS connection is needed. No `.env` loader is used; ordinary setup requires no environment variables.

Generated artifacts stay in this clone: `.venv/`, `.runtime/`, SDK/node_modules and `models/lettuce/`. The runtime profile records local executable paths, mode and device; rerun setup on another computer instead of copying it. The first setup asks for a `.test` email and hidden 15–128 character password. Existing owners are not replaced. The owner can add organizations and simulate synthetic member email proof, then use normal approval/role controls. Verification never grants workspace access by itself.

App: `localhost:8896`; Auth: `127.0.0.1:9099`. Occupied ports cause an actionable error; no foreign process is killed. One terminal supervises both services. `start.ps1 -NoBrowser` suppresses opening a tab. Ctrl+C snapshots and stops its own services.

Private databases/media live in `.runtime/local/{auth,science}/`. A serialized, atomic `identity-state.json` contains the emulator export plus SQLite backup payloads. Snapshots follow successful mutations and run during operation/shutdown. Pinned-emulator import restores `validSince`, preserving session revocation state. Startup imports identities without rolling live scientific SQLite backwards. Unknown storage, mismatched bindings and production environment settings are rejected.

Back up the **whole `.runtime/local/` directory while stopped**, including its ownership marker and identity export. It contains password hashes and private data: never publish it. A hard machine interruption can lose an in-flight request; successful mutations snapshot before returning. This local backup is not production disaster recovery. Never delete an existing research database to resolve an identity mismatch.

## Authorized model bundle

Promoted lettuce checkpoint: **exp006-segformer-001**, SegFormer B0 binary classifier, 14,885,476 bytes. SHA-256:

```text
92c66f6f2f0d0f1666ba7351b85b8eaeb071e3bfab2bb165a47d95b6354f0134
```

`configs/runtime/model-assets.json` pins all bundle files/hashes. Inference uses RGB/255, ImageNet normalization, 512-pixel letterboxing, native-coordinate restoration and threshold 0.5. Strict safetensors loading constructs the architecture without a pretrained download. No dataset, optimizer, encoder cache or torchvision is necessary for inference.

Terms: [NVIDIA SegFormer license](https://github.com/NVlabs/SegFormer/blob/master/LICENSE), [encoder card](https://huggingface.co/nvidia/mit-b0). The checkpoint lineage restricts use/derivatives to noncommercial research/evaluation with notices and other conditions. The private bundle preserves exact license, model card, Transformers license and dataset attribution. Notices do not grant rights. Supply it only to an authorized recipient whose use satisfies the terms. No public upload, new cloud download rights or commercial license is provided.

Authorized maintainer export from an existing immutable installation:

```powershell
py -3.11 -m src.local_setup.model_assets export --source-root 'C:\authorized-installation' --notices-root 'C:\authorized-notices' --bundle '.model-bundles/research-model-exp006.zip'
```

Install validates hashes, complete registry/config/provenance, paths and sizes; rejects corrupt/changed/duplicate/symlink archive members and conflicting destinations. Identical reinstall is a no-op. Never alter the allowlist to accept arbitrary weights.

FULL setup and startup run the actual model. Failure stops startup; requested learned lettuce inference never silently falls back to classical CV. DEMO is account/UI work without scientific readiness; models already installed are retained. Sorrel keeps its existing separate classical/manual functionality, without a validated learned model. All proposals require human review.

## CPU, GPU and WSL

Qualified native runtime: Python 3.11.9, Node 24.18, Torch 2.10.0+cu128, RTX 5090 Laptop sm_120, NVIDIA driver 610.88. CPU mode installs Torch 2.10.0+cpu. Auto selects CUDA only with an appropriate detected driver; actual tensor and inference probes follow. A selected-device failure is an error, not a silent switch. Device is retained in analysis provenance; production's default remains CPU.

`requirements-model-{cpu,cuda}.txt` use [official PyTorch indexes](https://pytorch.org/get-started/previous-versions/). Shared inference dependencies are pinned in `requirements-model-common.txt`. CPU/CUDA can differ at threshold-border pixels; no bit-exact cross-device guarantee is made.

The historical native c10.dll restriction did not reproduce in the qualified native runtime. Scripts do not alter App Control, drivers or Windows features. An existing WSL2 environment with Python 3.11 and Node 22/24 can instead use:

```bash
bash setup.sh --model-bundle /path/to/authorized/research-model-exp006.zip --device cpu
bash start.sh
.venv/bin/python -m src.local_setup.diagnostics
```

Use a **separate clone** and OS-specific venv/node_modules. WSL prerequisites are not installed automatically. Existing WSL CPU/CUDA inference was checked separately; it is not a full fresh WSL setup result. Windows is the primary acceptance target; no blanket macOS/Linux qualification is claimed.

## Tests

From an installed clone:

```powershell
.\check.ps1
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest tests/test_local_install.py tests/test_model_assets.py tests/test_leaf_instances.py tests/test_integrated_app.py tests/test_integrated_science_workflow.py tests/test_user_guide.py -q --tb=short --assert=plain
node tests/integrated_frontend_checks.cjs
node tests/firebase_access_checks.cjs
node tests/firebase_production_ui_checks.cjs
```

Stop the app first for tests that own the actual Auth emulator:

```powershell
$env:HYDRO_TEST_FIREBASE_TOOLS_ROOT = (Resolve-Path .runtime/firebase-tools/node_modules/firebase-tools).Path
.\.venv\Scripts\python.exe -m pytest tests/test_local_runtime.py -q --tb=short --assert=plain
Remove-Item Env:HYDRO_TEST_FIREBASE_TOOLS_ROOT
```

Without that variable the emulator tests skip. Skips are not persistence passes. `--assert=plain` avoids printing provider tokens through assertion introspection. Synthetic predictors test contracts, not learned accuracy. FULL `check` performs actual model inference and split/area invariants; browser editing/approval acceptance is separate. Release QA uses an original procedural CC0 image, actual SegFormer and explicit local synthetic measurement metadata.

Optional Firestore tests require Java 21 and the existing emulator-only harness, separate from SQLite setup. Historical experiments require authorized assets; asset-dependent skips do not establish scientific validation.

## Troubleshooting and boundaries

- Missing bundle: obtain it from an authorized maintainer, or explicitly choose DEMO.
- Identity/storage mismatch: retain all files and restore a matched backup; do not recreate identities over a scientific database.
- Port occupied: close the other app through its own terminal.
- Native dependency failure: confirm Python 3.11 x64, fix the stated prerequisite and retry. Use CPU mode when CUDA is inappropriate.
- PowerShell policy: follow your organization's approved process; scripts change no system-wide policy.
- Failed analysis: the photograph is preserved; correct the error and retry.
- Unattended first-owner QA can use `HYDRO_LOCAL_OWNER_EMAIL` and secret `HYDRO_LOCAL_OWNER_PASSWORD` instead of prompts. Remove them immediately; never write values to source/history/reports.

Never publish `.runtime/`, credentials, weights, bundles, photographs, databases or restricted datasets. `.gitignore` erases no history. Sanitized publication history remains separate from retained original development history. No production deployment, migration, retraining, promotion or recurring service is introduced.
