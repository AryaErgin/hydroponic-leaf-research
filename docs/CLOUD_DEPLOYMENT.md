# Deploy your own cloud instance

This installs an independent research website in **your own dedicated Google Cloud project**. It uses your Firebase users, Firestore database, private buckets, service accounts, secrets and billing. It never imports another installation's accounts, photographs, measurements, calibrations or credentials. The original production project and its number are explicitly rejected by the new setup/runtime.

**Current evidence:** implementation and local validation only. Actual cloud deployment, Linux container execution, managed email receipt, GCS persistence and cloud redeployment remain **UNVERIFIED**. No cloud resources or costs were incurred to validate this release. Passing local tests/emulators does not prove those live steps work; complete the checklist below in your own authorized project before research use.

## Prerequisites

- Access to this private repository and Git.
- Python **3.11 x64** and [Google Cloud CLI](https://cloud.google.com/sdk/docs/install). Docker and local Node are not required for the guided Cloud Build path. The wizard creates a pinned `.venv-cloud` for owner-operator tools; server Python/Node/Torch are built in the container.
- Your own Google account with permission to create a project, or an existing **empty dedicated project**. Organization policies may require an administrator to create it in the correct organization/folder first; then select existing. Unknown existing scientific databases, applications, accounts, buckets or secrets are never silently adopted.
- An open billing account you are authorized to link, plus the rights needed to enable APIs, manage the new project's IAM/service accounts, Firebase, Firestore, buckets, Artifact Registry, builds, secrets and Cloud Run. Creating budget alerts may require billing-administrator help. The wizard does not grant you organization/billing privileges.
- A controlled receiving email for your new owner account and subsequent real email acceptance. Your Google CLI identity and the website's Firebase account are separate identities, even when they use the same email.
- The authorized `research-model-exp006.zip` bundle. Obtain it privately from an authorized maintainer; see [model rights and export](DEVELOPMENT.md#authorized-model-bundle). Research/evaluation terms apply. Neither weights nor credentials belong in GitHub.

The initial supported region is **us-central1**. Firestore's initial location is a lasting choice. This release does not offer an untested region/multi-region migration path.

## Guided setup

```powershell
git clone https://github.com/AryaErgin/hydroponic-leaf-research.git
cd hydroponic-leaf-research
.\cloud-setup.ps1
```

Optional supplied answers:

```powershell
.\cloud-setup.ps1 -ProjectId your-own-research-project -Institution 'Your University' -ModelBundle 'C:\authorized-assets\research-model-exp006.zip'
```

The wizard:

1. Validates the real model/archive hashes, architecture, provenance and notices. Packages **committed** application source and the trusted model allowlist before cloud provisioning. Local private research files, `.env`, `.git`, `.runtime`, credentials and datasets are excluded. A ZIP source download without Git history is not the supported build path; commit reviewed source edits before deploying them.
2. Installs pinned operator dependencies, creates a separate non-active gcloud configuration and opens Google's own sign-in. It does not replace your currently active gcloud configuration or use service-account key files.
3. Selects your existing empty project or asks for explicit typed consent before creating one. If your organization restricts project creation, create an approved empty project in the Google console and rerun using its ID. Existing projects must already use the selected billing account: the wizard will not silently switch their billing or automatically link an unbilled existing project before it can fully inventory resources. For that case personally verify emptiness and link billing in the console, or choose a new wizard-created project.
4. Verifies project ID/number and installation ownership. You select your billing account and alert amount; it prints the resources and cost caveats. **Nothing is inferred from silence:** type the exact `DEPLOY <project-id>` phrase to authorize the plan. A changed source/resource/billing plan needs new consent.
5. Links your chosen billing account, configures project-scoped budget alerts where permitted, and provisions the infrastructure. If finance must create the alert manually, the wizard offers an explicit manual confirmation path and records that it was not API-verified. Alerts do not stop spending.
6. Adds Firebase to this project, creates its web app and uses standard Firebase email/password authentication. Google may auto-enable supporting Firebase APIs and service accounts. If personal terms acceptance or initial Auth activation is required, it opens your project's console: **Authentication → Get started → Email/Password → enable and Save**. Accept terms yourself. Do not choose an Identity Platform upgrade, SMS, Analytics or a separate email provider.
7. Configures the exact HTTPS Cloud Run domain, email/password and improved email privacy. It projects only safe Auth config fields; hash-signing configuration is never requested. The Firebase browser API key is a public client identifier, not a server credential; its API targets are narrowed to Identity Toolkit and Secure Token. Backend permissions remain authoritative. The wizard does not add browser referrer restrictions: the app deliberately sends no referrer, and adding them without a separately tested policy can break sign-in or Google-managed actions. Existing custom key constraints are not silently weakened.
8. Creates its own Secret Manager versions through stdin, privately uploads only the verified source/model context, and submits a bounded Cloud Build. This may take several minutes. No original production image/base container is required. Builds use public pinned base images and the real CPU checkpoint; real inference is a container build gate and a runtime startup gate. Failure never substitutes a synthetic or classical lettuce predictor.
9. Deploys one HTTPS Cloud Run service using an immutable image digest, with request-based CPU, **2 vCPU / 4 GiB / min0 / max1 / concurrency1**, and checks own-project scientific readiness. There is no GPU or always-on instance. The wizard then opens your website for owner registration.
10. Register your new Firebase website account in the initial institution, receive Google's verification email, open it, and sign in again. It remains **pending**. Enter only that exact verified email in the IAM-authenticated setup terminal. The operator verifies the Firebase UID and pending backend record before an atomic owner grant. No password/reset link/code is requested in the terminal; no public first-signup owner route exists. Sign out/in afterward to refresh the role.

On success it prints your website URL. Other users must verify email and receive administrator approval. The owner can create additional institutions and assign the permitted administrator roles. Membership requests cannot self-promote, and backend authorization controls every institution's scientific data.

Google's normal hosted web email actions handle signup verification, password reset and secure email changes. No sender mailbox, custom domain or SMTP provider is required. These flows still need real receipt tests on your installed project; API acceptance alone is not inbox delivery proof. New-account/signup errors are not guaranteed to conceal every email's existence, even with improved email privacy.

## What is provisioned

| Resource | Boundary |
| --- | --- |
| Cloud Run | One own-project HTTPS service; scale-to-zero and max1; immutable image |
| Firebase Auth | Own project/web app and users; verified email plus backend approval |
| Firestore Native | Own regional default database; durable accounts/sessions/science; distinct installation/tenant prefixes; browser rules deny all |
| Science GCS bucket | Uniform bucket-level access, public access prevention; backend-mediated scoped downloads |
| Build GCS bucket | Separate private staging; build identity cannot read the science bucket |
| Artifact Registry | Private own-project container repository |
| Runtime service account | Firestore use, scoped science object access, specific secret access, minimal Firebase user/session permissions; no hash-config permission |
| Build service account | Read build staging, write image repository/logs; no science-bucket grant |
| Secret Manager | Own random runtime root and own instance configuration, pinned versions; no service-account keys |

`infra/cloud/` and `src/cloud_setup/` define the reproducible infrastructure operations and container. `src/cloud_instance/` supplies the explicit independent runtime while preserving the existing local and legacy production adapters. No historical-data migration command is invoked.

## Resume, redeploy and rollback

Keep ignored `.cloud/instances/<project-id>/state.json` private and backed up. It records the installation ID, create intents, resource identifiers, pinned secret versions, image and revision—not OAuth tokens or passwords. **Do not delete it to resolve an error.** Ownership labels and state must agree; the wizard refuses unknown resources instead of recreating them. Rerun with the same project ID and model bundle after completing the indicated manual step. The same committed source can reuse its recorded image; a changed source commit is repackaged/reapproved.

Cloud Run local files are temporary. Scientific media lives in GCS and approved metadata/calibration snapshots in Firestore. The model is baked into the immutable image. Calibration changes remain prospective; no redeployment recalculates approved areas.

Build job IDs and receipt hashes are saved before polling, so an interrupted known job is resumed instead of buying a duplicate build. Closing the CLI does **not** necessarily cancel a queued/running cloud job: it can continue until its 300-second queue expiry / 1,200-second build timeout. Inspect your own project's Build console before retrying. A verified failed build requires a separate `RETRY BUILD` approval for one new attempt. If a request's outcome is unknown and no job ID was received, stop and reconcile it in that console; do not delete state or blindly submit another job.

The wizard retains the previous revision only after a successful own-project/model/storage readiness check, and if a candidate fails it attempts to restore traffic to that accepted revision. A failed first revision is not saved as a future healthy rollback target. It does not delete scientific data. If rollback itself cannot complete, stop and use your own service's Cloud Run console; do not call a failed revision healthy. New installations have no previous revision to restore.

Before your first redeployment, make and verify your own Firestore/GCS backup under your data policy. This tool does not claim a tested live backup/restore rehearsal or automatically delete retained versions. Old images, build sources, secrets, logs and stored research data can continue costing money at zero Cloud Run instances. Review retention/cleanup in your own project; deletion is an explicit administrator decision, never an automatic reset. Never apply cleanup commands to another project.

## Costs and safeguards

Prices vary by location, billing currency, free allowances and usage. Examples below use the [current Cloud Run us-central1 request-based prices](https://cloud.google.com/run/pricing), **before free allowances**:

- 2 vCPU × USD 0.000024/vCPU-second plus 4 GiB × USD 0.0000025/GiB-second = about **USD 0.000058 per active second**. 1,000 analyses using 60 billed seconds each would be about **USD 3.48 compute**, excluding other requests/startups/services. This is a scenario, not an inference-time or monthly bill guarantee.
- Light classroom/research usage may be a few USD/month or less when allowances apply. Heavy traffic or abuse can cost much more. At continuously active compute, the example configuration is roughly USD 150/month before free allowances and other services despite max1.
- Builds use the default [Cloud Build machine and 1,200-second timeout](https://cloud.google.com/build/pricing). [Image storage](https://cloud.google.com/artifact-registry/pricing), [GCS storage/operations](https://cloud.google.com/storage/pricing), [Firestore](https://cloud.google.com/firestore/pricing), [secrets](https://cloud.google.com/secret-manager/pricing), logs and network are separate. Persistent storage/retained versions are not free just because the server is idle.
- Standard [Firebase Auth quotas](https://firebase.google.com/docs/auth/limits) and the project's [Firebase billing plan](https://firebase.google.com/pricing) apply. A billed Cloud project must not be described as automatically Spark/free. No phone/SMS, Identity Platform upgrade, Analytics, Functions, Hosting or managed SQL is intentionally enabled by this installer.
- Project budget alerts use 50%, 90% and 100% thresholds. **They are not hard spending caps and can be delayed.** This wizard does not enable a separate spend-cap product, disconnect billing automatically, or promise that maximum instances caps every charge. See [Google's budget guidance](https://docs.cloud.google.com/billing/docs/how-to/budgets).

## Local validation and the required live checklist

An offline dry run authenticates nowhere and creates no cloud resources:

```powershell
.\cloud-setup.ps1 -PlanOnly -ProjectId your-own-research-project -ProjectNumber 123456789012 -Institution 'Your University'
```

The supplied example number is not a verified project identity. The real wizard obtains it from Google before provisioning.

Locally verified: configuration/project/credential guards, protected-project rejection, explicit consent, clean-source/model packaging, IAM plans, API field restrictions, interrupted-operation/resume and rollback logic with injected responses, owner bootstrap/institution UI contracts, legacy compatibility, and real Firestore emulator namespace isolation. These tests make no cloud/email/container success claim. Native Docker is unavailable in the validation environment; WSL Docker socket access was denied, and system permissions were not altered.

Before declaring **your cloud installation** ready, use isolated synthetic records and verify:

1. Fresh project deployment and actual Linux container build/startup with real SegFormer; no fallback when checkpoint is invalid/missing.
2. Owner registration, real verification email receipt/link, IAM bootstrap and durable owner login.
3. New verified member stays pending until approval; org administrator scope and platform role promotion work.
4. A second institution cannot retrieve the first's plants/photos/calibrations/reviews/measurements/masks/artifacts/CSV by changing IDs or URLs.
5. Real upload/calibration/proportional-reuse, model prediction, all mobile leaf tools, reviewed synthetic approval, mean/median/per-leaf metrics and populated history/graphs/CSV.
6. Firestore/GCS persistence, private object denial, auth revocation and approval safeguards; scientific hashes/calibration snapshots unchanged after cold start/redeploy.
7. Actual password-reset receipt/single use/revocation and email-change receipt using a second controlled inbox. Do not publish links/codes or count a send response as delivery.
8. Correct own-project secrets/IAM/public configuration, expected cost controls, owner access, usable iPhone layout and rollback to the prior healthy revision.

Do not approve synthetic images as genuine biology. Keep them explicitly synthetic and training-ineligible. Never import real records merely to test deployment. See the repository's status for the exact tests run in this release.
