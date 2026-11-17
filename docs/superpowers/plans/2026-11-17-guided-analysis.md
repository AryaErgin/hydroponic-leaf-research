# Guided analysis design and implementation plan

The detailed user brief authorizes this refinement. Preserve the incumbent analysis page, scientific policy, APIs, model and authentication. Use existing integrated isolated checkout; no production operations.

## Design

One page, seven automatically derived steps: Plant, Photo, Calibration, Analyze, Review, Approve, Results. Compact navigation identifies current/completed/optional states and revisits editable controls without resetting work. A concise current instruction and delegated next action lead to existing controls. Never automatically run inference, confirm review or approve. No progress percentages or fabricated cold-service detection.

Calibration is optional only for existing pixels-only support, and safely reused only according to actual server assessment. Before analysis a remembered calibration is pending per-photo server verification, not marked physically verified. Equal dimensions do not establish camera geometry. Returning users need no added confirmation unless existing server policy requires it. Optional tips persist under authenticated user ID and organization; no measurement fields store onboarding choices.

Review shows the native image/overlay prominently, grouped existing tools, short current-tool instructions and immediate existing edit feedback. Approval checklist derives from unchanged approvalState/calibrationIssue/editor stats. Direct repair actions preserve edits. Results show saved metrics, growth and another-photo controls.

## Implementation and evidence gates

1. Add independent pure workflow-state policy and tests for empty/preparing/retained photo/calibration optional, server reuse states, invalid masks, approved/resumed review, busy/errors. Observe failing tests before implementing.
2. Refine HTML/CSS using existing IDs. Integrate state rendering/actions in app.js, factual network phases and same-image retention during new-plant creation. Preserve upload retry/HEIC fixes; no backend/science edits.
3. Run three realistic journeys against real endpoints/model: novice synthetic approval, returning calibrated user, error recovery (points/upload/inference/unassigned/refresh). Isolate synthetic data and mark it before approval. Capture every stage at390px; check320/375/390/430 anddesktop in Chrome/WebKit, touch editing/zoom.
4. Update illustrated help to current real mobile UI if controls changed; privacy metadata and current-UI asset manifest tests must pass. Focused scientific/auth/security regression, independent review, public tree/history secret scan, no production changes.
5. Push tested source and open actual isolated integrated preview for user review. Final report includes commit, URL, screenshots, novice/returning behavior, tests, limitations and deployment readiness.
