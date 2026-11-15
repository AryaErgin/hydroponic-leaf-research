# Independent cloud infrastructure

Use the repository guided cloud setup entrypoint, with your **own dedicated**
Google Cloud project. The protected original project and its numeric identity
are rejected before any transport call. No active gcloud project switch, service
account key, original production configuration, private base image, or automatic
Identity Platform upgrade is part of this workflow.

The wizard presents the pure deterministic plan before applying it. Infrastructure
functions have an injected transport: importing, planning and offline tests make
no Google calls. Apply is the caller's explicitly consented operation. API enables
may precede resource inventory so disabled APIs cannot hide dormant resources.
Unknown existing databases, buckets, registries, services, custom service
accounts, custom roles, secrets or Firebase web apps stop setup. A fresh dedicated
project is required. Resume uses the persisted installation ID, remote markers,
validated resource shape and create intent recorded before remote creation.
No database or bucket is deleted or recreated by the installer.

Runtime has Firestore data access in its own dedicated project, object access on
the scientific bucket only, access to the two specific runtime/config secrets,
and a custom role containing only `firebaseauth.users.get` and
`firebaseauth.users.createSession`. The build service account has log writing,
image writing on its own repository, and source reading on the private build
staging bucket. It has no scientific bucket access. Google-managed service agents
and the human operator still need their normal platform permissions; an operator
must be allowed to enable services, create these resources and IAM policies, submit
builds, and act as the specific runtime/build identities. The installer never
grants owner/editor or creates permanent service-account keys.

Both buckets use uniform access and public-access prevention. Firestore Native
is regional, with deletion protection and browser-deny rules. Server SDK access
uses the runtime service account; browser-deny rules do not restrict server IAM.
The public HTTPS Cloud Run application enforces verified email, owner/admin
approval, and institution authorization in application code.

Firebase's personal terms/Authentication initialization may require opening the
selected project console, choosing **Get started**, and enabling **Email/Password**
with password required. Keep standard Firebase Authentication. The wizard resumes
by reading a narrow field whitelist, checks `subtype=FIREBASE_AUTH`, and updates
only the email provider and authorized domains. It never reads password hash
configuration. Firebase-managed verification/reset email actions retain the
project `firebaseapp.com` handler; the numeric Cloud Run host is authorized for
return navigation. App origin is derived before deployment using Google's
deterministic service/project-number/region URL contract.

The public image digests were resolved anonymously from Docker Hub/GCR. Python
3.11 and Node 24 are immutable base-image references; npm uses the committed
lockfile and Python uses the existing exact runtime/model pins. Linux wheels and
the full container must pass a real build; Windows qualification alone is not
Linux/container acceptance. The image runs as UID/GID 10001, writes scratch files
under `/app/.runtime`, verifies all authorized model asset hashes, and exercises
the actual CPU SegFormer predictor during build and runtime startup. No learned
fallback or downloaded pretrained model is permitted. Model licenses/notices
remain in the authorized model directory.

Build queue expiry is 300 seconds and execution timeout is 1200 seconds on the
default Cloud Build machine; logs go only to
Cloud Logging and build source to the separate private own-project bucket. Cloud
Run uses request-based CPU, 2 CPU, 4 GiB, min 0, max 1, concurrency 1, timeout 360
seconds and no startup CPU boost. These settings reduce idle/parallel costs but
are **not a hard spending cap**; transient scaling, builds, storage, egress, logs,
retained images, soft-deleted objects and secrets may incur charges. Billing
budget alerts are notifications, not caps. Review current provider prices and
configure an alert in the selected billing account before live acceptance.

Build submission is asynchronous. The installer saves the exact own-project job
ID and full verified source receipt, then polls that job. **Closing the CLI or
losing the network does not cancel a cloud build.** Rerun with the same source
and preserved setup state to inspect/resume the same job. Changed source cannot
start another job while the previous job is queued or running; wait for it to
finish or deliberately cancel that exact job in your own Cloud Build console.
The installer never automatically cancels or deletes jobs. A known failed job
does not automatically buy an identical retry: after verifying that exact job is
terminal, the wizard can request separate typed approval for one additional
billable attempt of the same verified receipt. Active or unknown jobs cannot be
overridden with that approval. If interruption happened before
the job ID reached the installer, setup stops with an unknown submission outcome
and requires deliberate reconciliation in the own-project Cloud Build console;
do not delete state to bypass that stop. This prevents blind duplicate spending.

Rollback retains prior revisions; pin concrete secret versions and immutable
image digests. A failed readiness probe must not be reported as completed live
acceptance. After acceptance, retain the previous revision identifier in local
installation state. To roll back, use the selected project Cloud Run console to
route traffic to that known prior revision; never delete science to repair a
deployment. Pause costs by disabling public invocation/stopping use and reviewing
retained resources. Destructive cleanup requires a separate deliberate operator
decision and backups; the installer provides no automated database/bucket delete.

Official contracts used during implementation:

- [Cloud Run deterministic URLs](https://docs.cloud.google.com/run/docs/triggering/https-request)
- [Cloud Run deploy flags](https://docs.cloud.google.com/sdk/gcloud/reference/run/deploy)
- [Cloud Run maximum instances can be exceeded briefly](https://docs.cloud.google.com/run/docs/configuring/max-instances)
- [Cloud Build user-managed service accounts and logging](https://docs.cloud.google.com/build/docs/securing-builds/configure-user-specified-service-accounts)
- [Cloud Build submit flags](https://docs.cloud.google.com/sdk/gcloud/reference/builds/submit)
- [Firebase Management project and web app setup](https://firebase.google.com/docs/projects/api/workflow_set-up-and-manage-project)
- [Firebase Authentication optional paid upgrade](https://firebase.google.com/docs/auth/)
- [Narrow Identity Toolkit project configuration](https://docs.cloud.google.com/identity-platform/docs/reference/rest/v2/projects/getConfig)
- [Firebase Auth IAM permissions](https://firebase.google.com/docs/projects/iam/permissions)
- [Private uniform buckets](https://docs.cloud.google.com/sdk/gcloud/reference/storage/buckets/create)
- [Firestore server SDK and browser-rule boundary](https://firebase.google.com/docs/firestore/security/insecure-rules)
- [Firebase Rules API](https://firebase.google.com/docs/reference/rules/rest/v1/projects.releases/patch)
