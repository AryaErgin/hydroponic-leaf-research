# Visual user guide ? 2026-11-16

Seven genuine annotated screenshots now illustrate the existing `/help` workflow: sign-in/plant, photo, calibration, analysis, colored-leaf editing, approval and history/CSV. Original synthetic artwork and empty/local test account UI only; no private photographs, account details or model weights. Seven optimized WebP assets total 274010 bytes, with alt text and native tap/keyboard enlargement. The Markdown guide uses the same images and copy.

Chrome mobile/touch emulation passed 320, 375, 390 and 430 CSS pixels and desktop1280: no overflow, all images loaded,35 image enlargements and5 keyboard enlargements passed. Physical iPhone Safari remains untested. Relevant guide/integrated/packaging tests passed; actual isolated SegFormer, calibration, Split, synthetic-only approval, history and CSV worked during capture. Scientific algorithms and authentication are unchanged. Public source/history scanning and image-content/metadata review gate publication. A guide-only live patch is prepared separately; production deployment is pending and no cloud operations occurred.

# Project status

2026-11-15 — **Independent cloud deployment tooling: locally validated; live deployment UNVERIFIED.**

The separate `cloud-setup.ps1` workflow supports a developer's own project, billing, Firebase identities, durable Firestore/session storage, private GCS, scoped IAM/secrets and real-model CPU container. It explicitly excludes the original production installation. Personal Google/Firebase/billing/model consent remains required by the person running it. No cloud resources, cost, deployment or production data changes were made for this milestone.

Local validation covers strict configuration/transport/resource guards, verified-UID owner bootstrap and safe contact updates, institution UI, source/model packaging, asynchronous build resume/explicit retry and healthy-only rollback. Real Firestore emulator namespace checks and local Auth/session regression tests ran. The browser SDK built identically on Windows/Linux. Exact container build, new-project email receipt, Google IAM/GCS and cloud cold-start/redeployment acceptance remain UNRUN. See [Cloud deployment](CLOUD_DEPLOYMENT.md) for costs/manual steps and the acceptance checklist. Existing local setup remains available below.

Final focused suite: **151 passed, 19 subtests passed**. Earlier broader regression: **233 passed, 1 skipped, 24 subtests passed** (Windows symlink privilege only). **22 Node suites, 19 independent UI assertions and 44 real Firestore-emulator assertions** passed. The suites overlap. Real CPU SegFormer inference from the packaged source/model context passed in 11.594 seconds; its procedural fixture is an engineering check, not biological validation. Linux web/Firebase installation, guarded server imports, locked SDK build and actual Torch 2.10.0+cpu matrix operation passed. The Linux common-model dependency installation exceeded the local qualification timebox: full Linux SegFormer inference and whole-environment dependency checks were not run. These results do not establish live cloud reproducibility.

2026-11-14 — **READY for authorized native Windows local reproducibility.**

The default FULL installation requires the approved private research model bundle once. The checkpoint is not distributed through GitHub. Setup pins dependencies, verifies the complete immutable registry and SHA-256, initializes isolated local accounts/storage and runs actual learned inference. DEMO is clearly marked and does not establish scientific readiness.

Fresh native CUDA installation and a direct GitHub fresh-clone CPU installation passed actual sign-in, local organization/member approval, calibration, SegFormer analysis, leaf editing, synthetic approval, history/graph/CSV and full restart persistence. Each run preserved all 17 scientific artifact hashes. The fixture is original procedural CC0 data; it establishes engineering functionality, not biological accuracy.

Direct GitHub clone: 53 Python tests passed, 1 Windows symlink-privilege skip, 5 subtests passed; 22 Node suites passed. Real Auth emulator persistence tests ran. No production credentials or training dataset were needed. Tree and publication-history secret scans passed; repository remains private.

Qualified runtime: Python 3.11.9, Node 24.18, Torch 2.10.0+cpu and 2.10.0+cu128 on RTX 5090 Laptop, Transformers 4.57.3. WSL inference was separately checked; full fresh WSL setup and physical Safari remain unqualified. Sorrel remains an unvalidated learned-model target. Human approval and projected-area semantics remain mandatory.

See [Development](DEVELOPMENT.md) for exact commands, model rights, runtime limitations and safe backups. No production changes or deployment were performed.
