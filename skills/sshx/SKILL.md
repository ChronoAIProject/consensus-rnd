---
name: sshx
description: Use when a high-risk or multi-angle decision needs worker-delegated inline consensus with isolated perspectives, fixed truth tables, and no daemon, GitHub, git, label, or release orchestration.
---

# sshx

`sshx` is a lightweight worker-delegated inline consensus skill. It applies the consensus engine philosophy to a single decision or implementation task by dispatching isolated worker perspectives without depending on any long-running runtime or lifecycle surface.

## Trigger

Use this skill when:

- a decision has meaningful product, architecture, correctness, safety, or cost risk;
- the user asks for multi-angle thinking, consensus, or review without starting a long-running work-unit loop;
- a concrete plan should be tested against independent perspectives before implementation;
- a finished change should pass a same-shape review gate before declaring done.

Do not use this skill for routine one-step answers where no separate perspectives would change the outcome.

## Goal Contract

`GoalArtifact` is a prompt-level record, not a runtime API. It is written during `intake` before worker mode selection or any worker dispatch.

`GoalArtifact` has exactly these fields:

- `raw_user_input`
- `normalized_goal`
- `constraints`
- `success_criteria`
- `iteration_question`
- `harness`
- `revisions`

`harness` is a prompt-level record containing exactly these three sub-items:

- `provided_capabilities`: capabilities already supplied by the execution environment that the skill must not implement again;
- `trust_boundary`: which roles are trusted and which are untrusted. The trusted declaration is **non-adversarial, not infallible**: failures, omissions, and uncertainty by a trusted party remain fully in review scope;
- `decision_ownership`: product, governance, and boundary decisions; engineering judgments; and orchestration judgments, each assigned to its owner.

`revisions` is an append-only list whose each item contains exactly these three sub-items:

- `change`: what was corrected;
- `authorization_source`: where authorization came from;
- `invalidated_completed_work`: completed work invalidated by the correction, or `none`.

A revision item missing any one of these sub-items is invalid and fails closed.

Any explicit correction to `GoalArtifact` or `harness` must append one such revision item before routing continues.

The caller must write and complete `harness` during `intake`, before any worker dispatch. If any `harness` sub-item is missing or ambiguous, or its source has not been confirmed by the boundary owner, stop and escalate to the maintainer; neither controller nor worker may infer or expand it.

The boundary owner may declare a host-provided goal-driven continuation mechanism only in `harness.provided_capabilities`; the skill must not discover or infer whether one exists. The termination gate is triggered only by a positive, boundary-owner-confirmed entry declaring such a mechanism. When an otherwise complete, unambiguous, boundary-owner-confirmed `provided_capabilities` value contains no such entry, whether silent or explicitly negative, the gate is inapplicable without asserting that the host mechanism is absent. A purported continuation entry that is ambiguous or unconfirmed is governed by the existing harness rule above.

The user's current input is the only source for the goal. `sshx` must not discover or infer the goal from external lifecycle milestones, release state, runtime host configuration, GitHub issues, GitHub pull requests, labels, branches, or any other external lifecycle surface.

`iteration_question` must ask what still differs from `GoalArtifact`, using the normalized goal, constraints, and success criteria as the fixed target. It must not broaden the task into a generic improvement search.

## InlineConsensusProtocol

`InlineConsensusProtocol` is a prompt-level protocol, not a runtime API.

Run the stages in this exact order:

1. `intake` (write `GoalArtifact` and normalize the goal)
2. `choose_worker_mode`
3. `thinking_panel_workers`
4. `meta_judge`
5. `implementation_worker`
6. `review_triplet_workers`
7. `fix_or_done`

`WorkerModeGate` is a prompt-level dispatch gate, not a runtime API. During `intake`, the caller may use its own read-only tools to inspect the user's input and write `GoalArtifact`; this caller-owned read-only intake is not worker dispatch. Before any worker dispatch, including delegated intake context-gathering by subagent, Agent, Task, or codex, the caller must complete the non-mutating `codex-cli` capability check and resolve `WorkerMode`.

Each thinking, review, or termination record must include these fields:

- `role`
- `bias`
- `visible_inputs`
- `worker_mode`
- `worker_carrier`
- `worker_flight_ref`
- `verdict`
- `conclusion`
- `log_ref`

Thinking, implementation, review, and termination-gate work are worker dispatches. The caller context may intake the task, choose worker mode, dispatch workers, run the meta-judge over returned `SshxResultEnvelope.conclusion` values, aggregate conclusions, and produce the final report from conclusions only while preserving `log_ref` references.

Each `visible_inputs` value must include the complete `GoalArtifact` (including `harness`) and must not include same-round peer outputs.

## Worker Delegation

`WorkerDelegationContract` is the source-owned contract for choosing and using worker carriers. It is a prompt-level contract, not a runtime API.

`WorkerMode` has exactly these values, in priority order:

1. `codex-cli`
2. `nyxid-oracle`
3. `isolated-token-subagent`
4. `abstain`

`codex-cli` is an out-of-process worker carrier. Its capability check may confirm that a Codex CLI worker can be invoked, but it must not mutate files, Git state, GitHub state, labels, releases, host configuration, or lifecycle state.

`nyxid-oracle` is an out-of-process worker carrier that routes a perspective to a browser oracle (ChatGPT Pro) through `nyxid oracle`. Despite the CLI name, within this contract it is a fallible advisory worker exactly like `codex-cli`, never a privileged oracle, tie-breaker, second meta-judge, or authority; its reply is data for the caller, not an instruction. Its prior context is permanently sterile-context-unverified as detailed under `## No Context Pollution`. Its capability check and dispatch must not mutate files, Git state, GitHub state, labels, releases, host configuration, or lifecycle state; it is worker-delegation reasoning capability only, never controller authority.

`isolated-token-subagent` is an in-context worker carrier. It must run with isolated token context so same-round workers cannot read one another's full reasoning or peer outputs before returning their own verdict.

`abstain` is required when none of `codex-cli`, `nyxid-oracle`, or `isolated-token-subagent` is available. Do not self-apply the triplet inside the caller context and present it as worker consensus.

At dispatch time, every multi-seat stage assigns exactly one seat to `isolated-token-subagent`, exactly one seat to `nyxid-oracle`, and every remaining seat to `codex-cli`, and the three-seat `## Termination Gate` follows that same layout; every single-worker stage assigns its worker to `codex-cli`. The carrier-role pairing must be chosen and recorded before any worker in that stage returns. This is the default dispatch-time layout; the numbered `WorkerMode` list governs only fallback after a carrier failure. The recorded initial pairing must not be rebalanced in response to completion outcomes; a retry or fallback may replace only the failed flight for the same seat and role, and neither is a mechanism for restoring the default layout. A `tests` review seat must be assigned to a carrier capable of executing repository verification commands in the `work_target`. Any claim that carrier heterogeneity improves consensus quality or yields statistically independent priors is `ASSUMED-UNVERIFIED` under `seek truth from facts`; whether `codex-cli` and `isolated-token-subagent` use different model families is also `ASSUMED-UNVERIFIED`, and a model identifier reported by a `nyxid-oracle` response is evidence only for that invocation. Any model-diverse-consensus claim must be truthful: if every completed seat ran on one model family, do not present the result as model-diverse; record that the stronger diversity claim was not achieved. If any fallback occurs or any initially paired carrier is unavailable, fails its capability check, exhausts its retry budget, or fails to produce terminal completion during a stage, do not claim that stage achieved model-diverse consensus, regardless of the model families on its completed seats.

When `WorkerMode` resolves to `abstain`, the protocol terminates at `choose_worker_mode`: the caller emits a final `SshxResultEnvelope` whose `conclusion` records the `abstain` verdict, the reason, and any options, creates no thinking, implementation, or review flight, and runs no later stage. When a thinking, implementation, review, or termination flight instead exhausts its bounded retries and fallback without terminal completion, that stage returns `abstain` rather than a synthesized worker conclusion or an incomplete triplet, the caller skips the remaining dependent stages, and the blocker is reported honestly. A thinking-stage exhaustion in particular skips `meta_judge`, `implementation_worker`, `review_triplet_workers`, and `fix_or_done`.

Every worker dispatch must create a prompt-level `SshxWorkerFlightRecord` before the worker is launched. The caller-carried transcript must keep these records under `worker_flights`, and each worker result record must reference the matching `flight_id` through `worker_flight_ref`.

`SshxWorkerFlightRecord` has exactly these fields:

- `flight_id`
- `stage`
- `role`
- `worker_mode`
- `worker_carrier`
- `work_target`
- `status`
- `retry_budget`
- `attempt`
- `result_envelope_ref`
- `completion_sentinel_ref`

`status` is one of `in-flight`, `retrying`, `terminal`, or `abstained`. `retry_budget` is a finite integer decided before the first launch for that flight. `result_envelope_ref` and `completion_sentinel_ref` are empty until a terminal worker result exists.

While any `SshxWorkerFlightRecord` for the same `work_target` is `in-flight` or `retrying`, the caller is read-only for that target. The caller must not mutate files, Git state, GitHub state, labels, releases, host configuration, lifecycle state, or the same external resource. The caller must not take over the same `work_target` because a process snapshot, log text, or workspace state appears quiet.

For each `codex-cli` attempt, before launch the caller must choose a unique `flight_id` and `attempt` and pass them to `skills/sshx/scripts/run-codex-worker.sh`; the runner derives and owns every artifact path, parallel attempts receive disjoint derived paths, and the caller must not supply arbitrary result, sentinel, log, or state paths. Every formal `codex-cli` flight must use this runner rather than a parallel direct-launch path. The caller invokes the runner, and the runner launches exactly one direct non-interactive worker carrier; neither layer may introduce a daemon or wrap the carrier in a repository-owned CLI. The command, sandbox, path, direct-process, and collection mechanics are owned by `CODEX_WORKER_SPEC.md`; the required dispatch shape is the runner's default `danger-full-access` sandbox, so the caller passes no sandbox selection unless the maintainer explicitly directs a narrower one. Time limits and final teardown of the whole job tree are the caller AI harness's responsibility. The runner does not propagate signals or manage carrier PIDs. The caller must not poll worker artifact paths while the runner is active. After the carrier exits, the runner performs one collection read of the derived `result_ref` and `completion_sentinel`; the caller records `result_envelope_ref` and `completion_sentinel_ref` on the matching flight only if the runner reports completion and the envelope and sentinel validate. Completion and verdict recognition stay governed by the `## Worker Completion Contract`.

The caller must launch the runner through a host-provided background job mechanism that notifies the caller when the carrier process exits. It must not use shell `&` to background the runner, because that detaches the process from host tracking and can leave an init-adopted carrier running without ever notifying the caller of completion. It must not monitor files or logs to poll for completion; doing so conflicts with the no-polling rule above.

`skills/sshx/scripts/run-codex-worker-batch.sh` is the permitted one-call fan-out alternative for the `codex-cli` subset of a multi-seat stage. It never covers a whole stage because the `nyxid-oracle` and `isolated-token-subagent` seats reserved by the dispatch-time layout above remain outside the batch. The dispatcher obtains every worker artifact path from the runner's pure path projection; worker artifact paths remain runner-derived and are never caller-supplied.

Internal shell `&` followed by `wait` is permitted inside that one named batch script because it remains the foreground process of one host-tracked job, records every child, catches `INT` and `TERM` subject to inherited signal dispositions, and joins every recorded child before publishing a report. If the host starts the dispatcher with `SIGINT` ignored, Bash cannot make its `INT` trap effective, so those signals are inert and the dispatcher can finish with `interrupted: false`; the `TERM` guarantee likewise presumes that `TERM` is trappable on entry. Its join-then-publish handler records the first interruption, ignores later `INT` and `TERM`, and repeats a wait only when that wait returned the recorded signal status so Bash can return the retained child status; it then marks the report interrupted and exits nonzero after publication. It does not forward the signal to runners or carriers and does not promise prompt carrier teardown; the runner may defer its own traps during the synchronous carrier call, and whole-job-tree teardown remains the host's responsibility. Caller-authored `&`, `nohup`, `disown`, and `setsid` remain forbidden. Batching degrades host completion notification from per-carrier to per-batch. Launching one host job per seat remains permitted and is the form on which per-seat retry and fallback latency depends; batching is an alternative, not a mandate.

The caller may invoke `skills/sshx/scripts/read-codex-worker-status.sh` only after host completion notification. Status reading is a one-shot, after-terminal collection convenience and is not authorization to poll while any runner is active. The batch report is dispatcher-owned orchestration evidence, not a worker artifact, and neither it nor the status projection changes completion or verdict routing.

For each `nyxid-oracle` attempt, the caller must start a new isolated oracle conversation before that attempt's first submission and pass a worker brief that requires the reply to be exactly an `SshxResultEnvelope` payload; parallel workers must receive disjoint conversations. The dispatch is a direct `nyxid oracle` reasoning invocation, not a helper script, daemon, or repository-owned CLI, and the exact command and flags are not part of this contract. Dispatch-exit recovery and all completion and verdict recognition for this carrier are governed solely by `## Worker Completion Contract` and are not restated here.

A `nyxid-oracle` worker has no access to the caller's filesystem, so caller-local paths, including `work_target` paths, are not readable content references for it. Its brief may instead reference repository content by public GitHub URL, pinned to an immutable commit SHA so every seat reads the same bytes; branch, tag, and `HEAD` URLs drift between reads and must not be used. Such a URL is permitted only when the referenced content is already anonymously readable on the remote, which the caller confirms before the first submission; the caller must never push, publish, change repository visibility, or otherwise mutate remote state to make content linkable. When the needed content is not already public, the brief inlines it instead. A referenced URL is worker context only: it is never a goal source under `## Goal Contract`, never a pointer to same-round peer output or another seat's artifacts, and whatever the oracle reports from it is worker-reported data rather than caller-verified evidence. If the oracle cannot retrieve a referenced URL, it must record that in `SshxResultEnvelope.conclusion` and mark every premise that depended on it `ASSUMED-UNVERIFIED` under `## Reasoning Discipline`, never reconstructing the content from memory.

When blindly redispatching the same `nyxid-oracle` attempt, the caller SHOULD reuse one stable submitter-scoped idempotency reference so repeated submissions converge on the same oracle task (for example, a carrier-supported client reference mechanism; this example is non-normative). A new attempt must use a new idempotency reference. This sentence does not itself authorize any redispatch; any blind redispatch must already be authorized by the existing bounded-attempt rules, and it never replaces same-task recovery.

`codex-cli` completion is recognized only when the caller has both a terminal `SshxResultEnvelope` and the worker-owned `completion_sentinel_ref` recorded on the matching flight. `pgrep`, process-table snapshots, log marker strings, and empty `git status` output are never completion evidence.

If an initially paired carrier is unavailable before a flight can be opened, the caller records the unavailable origin in `worker_delegation.reason` and the gate record, then immediately applies the fallback selection rule below without claiming that a same-carrier retry budget was exhausted. If any flight lacks terminal completion after its finite same-carrier retry budget is exhausted, the caller marks that flight `abstained` with empty `result_envelope_ref` and `completion_sentinel_ref`. In either case, when an eligible untried carrier exists, the caller must reopen the assignment on the highest-priority eligible untried carrier from the full `WorkerMode` list, rather than continuing strictly downward from the failed carrier; the chosen carrier must satisfy this stage and role's carrier constraints and must not have been tried for that stage and role. The caller creates a new `SshxWorkerFlightRecord` for the same `stage`, `role`, and `work_target`, and `worker_delegation.reason` and the gate record state the exhausted or unavailable origin and chosen fallback. The caller stays read-only for that `work_target` until the fallback flight reaches `terminal` or `abstained`. Only when no eligible untried carrier remains or every fallback fails to produce terminal completion is the result `abstain`; the caller must not implement, repair, or otherwise mutate the same `work_target` itself.

When a `codex-cli` attempt terminates with runner `reason_code` `ENVELOPE_INVALID` (which, by the runner's decision order, mechanically means that the carrier exited `0` and `result.json` exists but is invalid), and the same carrier's predeclared `retry_budget` still has capacity, the caller may include opaque path references to that seat's own immediately preceding attempt artifacts (`result.json`, `last-message.txt`, and diagnostic logs) in the brief for the next incremented attempt with the same `stage`, `role`, and `work_target`. The caller must not open, summarize, or repair those artifacts. The retry worker may read only its own immediately preceding attempt artifacts, never same-round peer output. It must first confirm for itself that the predecessor artifacts contain complete analysis reusable for the task. If it cannot confirm that, it must not package incomplete content into a terminal envelope; that attempt follows the existing ordinary retry and fallback path. If it can confirm reusable analysis, it must preserve that analysis, only reassemble a valid envelope, and publish its own new `result.json` and `completion.sentinel`. That attempt consumes the predeclared `retry_budget`. This packaging-only retry covers only envelope assembly failure; `VERDICT_INVALID` and every other `reason_code` follow the existing ordinary retry and fallback path.

## Result Envelope

`SshxResultEnvelope` is a prompt-level record, not a runtime API. Every `SshxResultEnvelope` returned by `thinking_panel_workers`, `meta_judge`, `implementation_worker`, `review_triplet_workers`, and `fix_or_done` uses exactly these top-level fields:

- `conclusion`: compact structured result consumed by the caller. It may include verdicts, decisions, blocking goal gaps, final decision points, changed-file evidence, and test evidence when applicable. It must not include process logs, step-by-step reasoning, raw transcripts, debug output, or same-round peer output.
- `log_ref`: artifact reference for the non-inline worker, meta-judge, implementation, review, or fix log, treated as an opaque diagnostic pointer. Caller-side routing, meta-judging, worker briefs, and final reports must not open, inline, summarize, or otherwise consume its content; they keep only the reference. Opening the artifact is allowed only for out-of-band debugging outside the consensus decision context.

`conclusion` is a structured JSON object, not a free-text string, and `log_ref` is a non-empty string reference. When a stage requires a verdict, it is the string at `conclusion.verdict`.

A caller-carried stage record wraps this envelope: it references the envelope's `conclusion` and `log_ref` and may add only the stage metadata named by `InlineConsensusProtocol` and the `## Transcript Template` (such as `role`, `bias`, `visible_inputs`, `worker_mode`, `worker_carrier`, `worker_flight_ref`, `verdict`, and the `meta_judge` and `fix_or_done` `exit`, `concrete_plan`, `goal_gap`, and `next_iteration_question`). The envelope payload itself stays exactly `conclusion` and `log_ref`. A stage record's `verdict` field, when present, is a read-only mirror of its envelope's `conclusion.verdict`; `conclusion.verdict` is the sole verdict source for routing, the two must be equal, and any mismatch fails closed.

Logs are not inline in caller context. Final reports aggregate `conclusion` values only and retain `log_ref` references for optional inspection.

## Worker Completion Contract

For `codex-cli` workers, caller-side completion and verdict routing must be decided only from:

- the worker carrier process has exited with status `0`;
- the runner-derived, runner-owned `result_ref` artifact exists;
- the `result_ref` artifact parses as a valid `SshxResultEnvelope`;
- `conclusion.verdict`, when the stage requires a verdict, is present and is one of that stage's allowed verdict values.
- the runner-derived, runner-owned `completion_sentinel` artifact exists and is recorded as `completion_sentinel_ref` on the matching `SshxWorkerFlightRecord`.

A worker is not done while its carrier process is still running, even if a partial `result_ref` artifact already exists. A worker is not done when completion markers or verdict-looking text appear only in stdout, stderr, raw transcripts, final text, prompt echoes, `log_ref` content, or log tails. Those surfaces are diagnostic only and must not participate in done detection or verdict routing.

`log_ref` remains required as a diagnostic artifact reference, but it is never a verdict source. Missing `conclusion`, missing `log_ref`, placeholder verdicts, and verdicts outside the stage's allowed set fail closed.

Neither runner-owned `status.json` nor a dispatcher-owned batch report is a completion or verdict source.

For `nyxid-oracle` workers, outside the bounded dispatch-exit recovery below, the caller must not poll or busy-poll the oracle task while it runs. On the normal path, when an attempt's dispatch invocation reports a structured terminal status, that attempt does not enter recovery: the caller performs one bounded `nyxid oracle result` read, and that read is the attempt's single collection read. If that collection read's output is missing or unparseable, including empty or truncated output or output without a parseable structured status/result wrapper, it is not terminal completion: the matching flight becomes `abstained` and follows the origin-agnostic fallback rule under `## Worker Delegation`.

An attempt enters dispatch-exit recovery only when both of these conditions hold: its dispatch call successfully submitted the oracle task and the caller recorded that task's oracle task reference; and the dispatch call then exited unexpectedly before reporting a structured terminal status. If the oracle task reference is unavailable, the attempt does not enter recovery: the matching flight becomes `abstained` and follows the origin-agnostic fallback rule under `## Worker Delegation`.

For an attempt that enters recovery, the caller may perform a finite recovery read sequence against that same oracle task. The read-count upper bound and delay schedule must be chosen and recorded before the first recovery read. Every scheduled delay must be positive and non-decreasing, and the entire sequence remains subject to the caller harness's task deadline. A recovery read that returns a structured non-terminal status is only a waiting recovery observation: it is not that attempt's collection read and provides no completion or verdict evidence. A recovery read whose output is missing or unparseable is likewise only a waiting recovery observation: it consumes one predeclared recovery read slot, is not that attempt's collection read, and provides no completion or verdict evidence; if a slot remains, the recovery sequence may continue. The first recovery read that returns a structured terminal status becomes that attempt's one and only collection read; after that terminal read, the caller must perform no additional reads for the attempt.

Terminal completion is recognized only when both of these conditions hold: the attempt's unique collection read returns the oracle task's structured terminal `status=completed`, and the `response` payload parses as a valid `SshxResultEnvelope` with any required `conclusion.verdict` in the stage's allowed set. Any of the following is not terminal completion and makes the matching flight `abstained` under the origin-agnostic fallback rule in `## Worker Delegation`: a unique collection read that returns any status other than the structured terminal `status=completed`; a `completed` collection read whose response envelope or required verdict is missing or invalid; or exhaustion of the recovery sequence without any structured terminal status. Intermediate task statuses (`queued`, `dispatched`, and any non-terminal phase) are not completion. This bounded dispatch-exit recovery is not a polling authorization; the ordinary polling and busy-polling prohibition remains unchanged.

This recovery is grounded in verified carrier behavior: the oracle task's server-side lifecycle is independent of the waiting client invocation, and `result` reads are non-destructive state reads. This carrier has no worker-owned independent completion sentinel. After terminal completion, the caller records `result_envelope_ref` on the matching flight and records `completion_sentinel_ref` there as `n/a`. The oracle task is traceable through the `result_envelope_ref` recorded on that flight and the stage record's `log_ref`; response prose, stdout, echoes, and log tails are never completion or verdict evidence.

For `isolated-token-subagent` workers, terminal completion is recognized only when the isolated subagent returns a valid `SshxResultEnvelope` to the caller, with any required `conclusion.verdict` in the stage's allowed set; this carrier runs in-context rather than as a separate process, so it has no completion sentinel and its `completion_sentinel_ref` is recorded as `n/a`. The same isolation, fail-closed, and no-stdout-evidence rules apply. Missing or invalid output is not terminal completion: the flight becomes `abstained` and follows the origin-agnostic fallback rule under `## Worker Delegation`.

## No Context Pollution

The caller context must not carry worker full reasoning or same-round peer outputs. It may carry only:

- intake inputs and constraints;
- dispatch briefs sent to each worker;
- `SshxResultEnvelope.conclusion` values, including verdicts and explicitly surfaced blockers;
- `SshxResultEnvelope.log_ref` artifact references;
- final reports that aggregate conclusions only.

Input isolation and prior sterility are separate dimensions. As a hard invariant, no worker may see a same-round peer output or caller-conversation transcript content that was not explicitly included in its dispatch brief or `GoalArtifact`; if this isolation is unavailable, exit through `abstain` instead of degrading the protocol into single-context roleplay.

A seat's own immediately preceding attempt artifacts are not same-round peer output; referencing them under the narrow packaging-only retry in `## Worker Delegation` does not constitute context pollution.

Prior sterility is weaker and none of the allowed carriers provides it: `codex-cli` inherits repository `CLAUDE.md` or `AGENTS.md` context, `nyxid-oracle` may inherit unknown and uncontrollable account memory and project context, and `isolated-token-subagent` inherits `CLAUDE.md` and the caller's `MEMORY.md`. All three still count as independent seats, but none may be described as context-sterile or cited as evidence that their priors are independent. The oracle seat is permanently sterile-context-unverified. Each seat must disclose these inherited context sources in its existing `visible_inputs` value and state whether each source is unknown or uncontrollable, using `repo-prior-exposed` for `codex-cli`, `external-prior-exposed` for `nyxid-oracle`, and `caller-prior-exposed` for `isolated-token-subagent`; these are disclosure labels, not new fields.

## Reasoning Discipline

`## Reasoning Discipline` is the single source of truth for the reasoning pass used by `## Thinking Panel`, `## Review Triplet`, and `## Termination Gate`. It is prompt-level guidance only: not a runtime API, not a daemon, not a CLI, not a parsed schema field, not marker data, not lifecycle authority, and not a second transcript channel. The stages and gate reference this section; they do not restate it.

sshx's essence is independent context-isolated perspectives that oppose ugliness and waste to converge on an answer that is both beautiful and worth its cost.

Reference-frame: each thinking, review, or termination perspective identifies the applicable mature theory, engineering principle, industry best practice, mature industry case, mature pattern, or constraint framework governing this class of problem or implementation; surfaces the known-good shape; then re-checks each candidate conclusion, implementation interpretation, repair candidate, or termination judgment against it before settling the verdict. `no applicable mature theory found` is an acceptable explicit fallback; in that case the note says so and still records the root-cause and minimal-path re-check against `GoalArtifact`.

Aesthetic/adversarial: give a symmetric 美不美 (is it beautiful?) verdict for each candidate approach weighed, including the chosen, revised, rejected, or repair approach — beautiful, mixed, or ugly, earned from evidence, not a presumed indictment. Name any specific locatable ugly defect, or state `no material defect found` when none exists; where a defect exists, state why the approach is ugly as a specific locatable defect and what the beautiful form would be. Ugly defects include leaked abstraction, duplicated source of truth, special-case, bad coupling, asymmetry, lying name, hidden intent, or unverifiable premise. The beautiful form is the smaller, symmetric, single-responsibility, single-source-of-truth, intent-revealing form that satisfies `GoalArtifact` — smaller, not maximally complete; gold-plating past `GoalArtifact` is itself an ugly defect, not beauty. Beauty judges the coherence and integrity of the form that remains; whether any element is unnecessary is `parsimony`'s question, and whether the whole intervention is worth its cost is the `worth` seat's — beauty must not become a second parsimony or worth vote.

seek truth from facts: verify every factual premise against actual evidence before relying on it. Evidence examples include source artifact or line, current file contents, command result, test assertion, visible input, implementation-worker conclusion, or declared `GoalArtifact` constraint. Any assumed-not-verified premise must be explicitly marked `ASSUMED-UNVERIFIED` in `SshxResultEnvelope.conclusion` and either verified before routing, treated as a `GoalArtifact` goal gap, or used as an abstain trigger. A perspective must never silently rely on an assumed premise.

`CapabilityOverlap` is the candidate-solution boundary check: ask whether a candidate takes over a capability already declared in `harness.provided_capabilities`, or changes a decision assignment in `harness.decision_ownership`; either hit is an overlap and therefore out of bounds. `ThreatEligibility` is the review-finding boundary check: ask whether a finding would exist only if a role declared trusted by `harness.trust_boundary` deliberately acted maliciously; if so, the finding is ineligible. Trusted-party failure, omission, and uncertainty are always eligible. These are independent checks that share the `harness` fact source.

`DecisionGrounding` is the decision-input admissibility check shared by candidate solutions and review findings: no inadmissible input receives implementation work or blocking authority. For predicted harm, name a current path through which the predicted harm is reachable — a current call site or input path, an observed failure that demonstrates reachability, or a `GoalArtifact` term that makes the harm reachable. `DecisionGrounding` judges only admissibility, never evidence strength; how well an admissible premise is evidenced stays with `seek truth from facts` and its existing dispositions, which this check neither repeats nor overrides. For preventive work — a defense, validation, abstraction, or compatibility path — name a current consumer (an existing call site) or an explicit `GoalArtifact` demand — a `normalized_goal` clause, `constraints`, or `success_criteria` item; a test introduced together with the defense under judgment may corroborate grounding but never creates it, or the defense would ground itself. For blocking detail, this is the rabbit-holing limb, not an aesthetic matter: name the exact `GoalArtifact` term it prevents satisfying and pass the deletion counterfactual — if omitting the detail changes no named `GoalArtifact` decision, it does not block; depth past that point is not thoroughness. Failure is objective, not semantic: an input fails `DecisionGrounding` only when it names none of the bases its applicable limb requires — a current path through which the predicted harm is reachable, a current consumer (an existing call site) or an explicit `GoalArtifact` demand — a `normalized_goal` clause, `constraints`, or `success_criteria` item, or the exact `GoalArtifact` term the blocking detail prevents satisfying together with the deletion counterfactual. A named basis that evidence shows to be false no longer counts as a named basis, so the input is inadmissible on that basis. An input that names one whose correctness is disputed has disputed grounding, not absent grounding, and keeps its full blocking force until the dispute is settled against evidence; no one may declare an input ungrounded merely because its named basis is unpersuasive. This check removes no actual defect: a reachable failure, a trusted-party mistake, an omission, and a stated uncertainty stay grounded regardless of how expensive, inconvenient, or late the repair is. An ungrounded input may be recorded as advisory, but it must never be the sole basis of a `revise`, `reject`, `abstain`, blocking finding, `unsatisfied`, or any element of a concrete plan. `DecisionGrounding` asks only whether a decision input is admissible; `ThreatEligibility` asks who the actor is; `parsimony` asks how much mechanism; `proportional-containment` asks how far it binds; `worth` asks whether to pay at all; and the aesthetic verdict asks whether the remaining form is coherent. It is a third independent check sharing the `GoalArtifact` and `harness` fact sources with the two above.

Each thinking, review, or termination worker must surface one compact free-form reasoning-discipline note in `SshxResultEnvelope.conclusion` naming the reference frame, stating the known-good shape and alignment, deviation, or revision status; stating the aesthetic verdict (美不美) with the specific ugly defect and beautiful form, or `no material defect found`, for each candidate weighed; and stating the verified-premise or `ASSUMED-UNVERIFIED` status needed for the verdict. This does not override `GoalArtifact`, assigned bias or review focus, truth tables, or allowed verdict sets, and it is not mandatory citation work, not a literature search, not a parsed schema field, not marker data, not lifecycle authority, and not a blocker for valid `abstain`, `reject`, or `comment` outcomes.

## Thinking Panel

Run six whole-picture philosopher seats before choosing a plan — the same universal judgment lenses the consensus engine debates with. Each seat is one independent, context-isolated perspective that attacks from its own objective; the seats can and do disagree, and the meta-judge converges them:

- `teleology`: purpose and inevitability. What is this for, and is the form forced by that purpose? Attacks skipped-purpose and missing-inevitability.
- `parsimony`: economy. Delete until nothing is left to delete; every element must prove its right to exist. Attacks magic numbers, symptom branches, and machinery that has not earned its place.
- `fidelity`: truth over proxy. Does it measure the real thing, and is every premise verified at its source? Attacks proxy-over-truth and narrative-over-verification.
- `natural-ownership`: locus dyad, ownership pole. Which layer naturally owns this invariant, duty, or constraint — the layer with semantic responsibility and causal control? Attacks symptom patches, duplicated enforcement, and invariants forced onto consumers of what a producer should own.
- `proportional-containment`: locus dyad, containment pole. How far may this intervention rightfully bind, across scope, authority, and duration, given the evidence? Attacks over-hoisting, speculative abstraction, and turning a local fact into universal law.
- `worth` (值不值 — is it worth it?): decision value. Compare the candidate against doing nothing and against the cheapest sufficient alternative, then weigh its incremental expected benefit toward `GoalArtifact` against its total lifecycle cost — build and verification effort, recurring maintenance burden, complexity debt, failure and misuse risk, reversibility, delay, and the opportunity cost of the more valuable work it displaces. Attacks not-worth-it machinery, elegance `GoalArtifact` does not need, and cost that outruns benefit; it may reject a candidate every other seat finds beautiful and well-owned. It must not cut a capability `GoalArtifact.success_criteria` requires to save cost, and it must state its best counterfactual and the decisive cost/benefit assumption rather than fabricating a numeric ROI.

`natural-ownership` and `proportional-containment` are a coupled **must-clash locus dyad**: they run together, each must answer the other pole's claim, and they converge on the natural owner layer — not the highest layer imaginable. Ownership pulls the fix toward the layer that owns the invariant; containment resists over-reaching past it. This is the "go upstream to the root, but not past the natural owner" balance expressed as two adversarial seats the meta-judge converges, rather than a single balanced checklist.

`worth` (值不值) is an independent objective, not the aesthetic lens repeated: `parsimony` asks how much mechanism, `proportional-containment` asks where and how far it binds, and `worth` asks whether to pay for this at all, at this cost, now, versus the best alternative. A candidate can be minimal, beautiful, and properly contained yet still not worth doing, and a less minimal candidate can still be worth doing when the avoided downside justifies it. Because it is a seat rather than a cross-cutting lens, `worth` is judged once by its own perspective, so the panel is not homogenized into every seat re-deriving the same value verdict.

Before proposing, revising, rejecting, or abstaining, each seat must apply `## Reasoning Discipline` to every candidate conclusion it weighs and surface the compact reasoning-discipline note in `SshxResultEnvelope.conclusion` before returning a verdict.

When proposing, revising, or rejecting a candidate, each seat must state whether it hits `CapabilityOverlap`; a hit is an unclosed goal gap and must not enter `implement`.

Each seat applies `DecisionGrounding` to every proposed plan element and every `propose`, `revise`, `reject`, or `abstain` basis, and states the named current path, current consumer, or `GoalArtifact` term that makes each basis admissible. An ungrounded basis is not a goal gap, must not by itself hold a candidate out of `implement`, and machinery that only defends against one must not enter a proposed plan.

Every seat must first identify the problem essence or root cause implied by `GoalArtifact`, then frame `propose`, `revise`, `reject`, or `abstain` as an answer to it: what satisfies it, what still differs from it, or why it cannot be satisfied. A plan that only patches a surface symptom while leaving that root cause in place does not satisfy the thinking gate. `revise` must name the goal gap and a next iteration question; it must not open an unrelated design search.

Each seat returns one of:

- `propose`
- `revise`
- `reject`
- `abstain`

## Design Truth Table

The meta-judge applies this fixed thinking truth table:

| Inputs | Exit |
|---|---|
| unanimous actionable plan | `implement` |
| close disagreement with compatible plans | `meta-layer convergence` |
| bounded true stall | `abstain/escalate with options` |
| any attempt to use one perspective as consensus | `reject fake consensus` |

`meta-layer convergence` must produce one concrete plan before implementation. If the bounded pass still cannot produce a concrete plan, stop with options instead of inventing agreement.

An `implement` exit requires the concrete plan to be both beautiful (per the `## Reasoning Discipline` aesthetic verdict) and worth its cost (per the `worth` seat). A concrete plan that fails the 值不值 (worth) judgment is an unclosed `GoalArtifact` goal gap: before `implement` the meta-judge must rebut the `worth` seat's factual premises, choose a cheaper sufficient alternative, show the omitted benefit clears the goal threshold, or record an explicit owner-level acceptance of the cost — otherwise the exit stays `meta-layer convergence` or `abstain/escalate with options`, never `implement`. Beauty and worth are a conditional challenge, not a forced clash: when the beautiful form carries a material elegance premium over the cheapest sufficient form, `worth` must justify or reject that premium; when `worth` prefers a cheaper, uglier form, the meta-judge records the accepted debt with its owner, containment boundary, and removal or expiry condition, since `temporary` without an expiry condition is not acceptable.

An `implement` exit also requires no unresolved harness overlap or authority gap. A goal and its harness mismatch when it presupposes missing host/controller execution capability or asks the skill to repeat a capability already declared by the harness; the skill's own judgment responsibilities are not a mismatch. Route a mismatch as a goal gap to the maintainer rather than implementing it.

At this existing `meta_judge` implement-exit gate, reflect on whether the goal or harness changed and whether current evidence has overturned the direction. Emit exactly one concrete action with its responsible party: `continue`, `revise`, `stop`, or `escalate`.

When a seat's `SshxResultEnvelope.conclusion` records both a dedicated-domain objection and its falsifiable causal prediction, and the meta-judge's proposed convergence has not refuted that causal chain, the meta-judge must run a `FocusedRound` before converging, provided the objection passes the `DecisionGrounding` prerequisite below. An objection that objectively fails `DecisionGrounding` does not trigger a `FocusedRound`; for this prerequisite, the meta-judge checks only whether the seat named any admissible basis at all and must not assess its persuasiveness. An objection that named a basis whose correctness is disputed still triggers the round because disputed is not absent. When the meta-judge declines a round on this ground, it records that decline in the existing `finding_downgrades` record under the same own-words requirement that governs downgrades. The three conditions must all hold simultaneously: the objection recorded in that conclusion is in that seat's exclusive domain (for example, mechanism necessity for `parsimony`, purpose-forced form for `teleology`, or cost worth for `worth`); the causal prediction recorded in that conclusion is falsifiable rather than a preference; and the meta-judge's proposed convergence has not answered that causal chain, including when it answers only a secondary point. In the focused round, all seats independently answer one question: "Does this causal chain hold, and if it does, how should the plan change?" The round does not reopen design search and preserves `## No Context Pollution`. The same causal chain triggers at most one focused round; if disagreement remains afterward, escalate to the maintainer rather than continuing. A focused round consumes the existing bounded-pass budget.

An objection that fails `DecisionGrounding` under its objective-failure rule is not an unclosed `GoalArtifact` goal gap and does not by itself hold the exit out of `implement`: the meta-judge records it as advisory in the existing `finding_downgrades` record and records there the path, consumer, or `GoalArtifact` term the objecting seat itself named, or that it named none, using the objecting seat's own words and never a paraphrase. Disputed grounding stays blocking. This is not permission to set aside a reachable defect.

The convergence question must be "what still differs from `GoalArtifact`?" expressed against the fixed normalized goal, constraints, and success criteria. Do not generalize the convergence pass beyond that goal gap.

Before any `implement` exit, the meta-judge must include in its `meta_judge.conclusion` a compact free-form ASCII relationship diagram built only from `GoalArtifact` and the returned `SshxResultEnvelope.conclusion` values: nodes are the goal subquestions or goal-gap items, the six philosopher-seat stances (`teleology`, `parsimony`, `fidelity`, `natural-ownership`, `proportional-containment`, `worth`), and the concrete plan; edges are labeled `agree`, `conflict`, `depends-on`, `resolved-by`, or `converges-to`. The diagram rigidly constrains convergence: every surfaced subquestion or goal-gap node must appear, the concrete plan must resolve every `conflict` edge — including the `natural-ownership` vs `proportional-containment` locus clash and any `worth` not-worth-it edge — and any unresolved `conflict` edge is an unclosed `GoalArtifact` goal gap, so the exit stays `meta-layer convergence` or `abstain/escalate with options`, never `implement`. The diagram is free-form prompt-level content synthesized from conclusions only; it must not inline worker full reasoning or same-round peer output, and it is not a parsed schema field, marker data, lifecycle authority, or a blocker for valid `abstain` or `reject fake consensus` exits.

## Implementation Worker

Implement only the concrete plan approved by the thinking gate. Keep the implementation boundary narrow and state any deviation before making it.

`sshx` does not grant permission to commit, push, merge, close issues, edit labels, publish releases, or mutate external lifecycle state.

Implementation must be delegated to a worker using the stage's default carrier under `WorkerDelegationContract`. The caller context may pass the approved concrete plan and constraints, then receive `conclusion` and `log_ref`; changed-file and test evidence belong in `conclusion`, and process logs stay behind `log_ref`.

## Review Triplet

After implementation, run three review perspectives:

- `architecture`: boundaries, contracts, coupling, and maintainability.
- `quality`: behavior, edge cases, failure modes, and user impact.
- `tests`: coverage, determinism, and verification strength.

Reviewers must check protocol text for newly added exception clauses, statements that contradict existing clauses, and semantic weakening of existing propositions.

Before approving, commenting, or rejecting, each reviewer must apply `## Reasoning Discipline` to every implementation interpretation, repair candidate, or approval path it weighs and surface the compact reasoning-discipline note in `SshxResultEnvelope.conclusion` before returning a verdict.

Each reviewer returns one of:

- `approve`
- `comment`
- `reject`

## Review Truth Table

The meta-judge applies this fixed review truth table:

| Inputs | Exit |
|---|---|
| any explicit reject | `fix` |
| no reject and at least one approve | `done with advisory surfaced` |
| all comment and no approve | `explicit user decision or another bounded review pass` |

Advisory comments do not count as approval. A reject blocks done until the issue is fixed or explicitly converted into a non-blocking advisory by a bounded review pass.

Every blocking finding must explain which `GoalArtifact` term it violates and which class of failure, omission, or uncertainty within the declared trust boundary it addresses, and must state its applicable `DecisionGrounding` basis under `## Reasoning Discipline`. A blocking finding that fails `ThreatEligibility` or `DecisionGrounding` is downgraded by the meta-judge to an advisory with its reason recorded, then the remaining verdicts are routed again. A `DecisionGrounding` downgrade obeys its objective-failure rule and records the current path, current consumer, or `GoalArtifact` term the finder itself named, or that it named none, using the finder's own words and never a paraphrase; disputed grounding stays blocking. Downgrade is allowed only for threat-model ineligibility or an ungrounded input, never because a finding is inconvenient, expensive, or late, and never sets aside a reachable defect. A missing, ambiguous, or stale harness declaration is never a downgrade shield: pause routing and escalate to the maintainer instead of declaring done.

Before each fix or repeated review pass, use the existing gate to ask whether the goal or harness changed and whether evidence overturned the direction; emit exactly one concrete `continue`, `revise`, `stop`, or `escalate` action and name its responsible party.

## Fix Or Done

If review exits `fix`, ask what still differs from `GoalArtifact`, apply the smallest change that addresses that blocking goal gap by delegating it to a worker using the stage's default carrier exactly as `## Implementation Worker` requires - open a new `SshxWorkerFlightRecord` for the same `work_target` and stay orchestration-only for the repair - then rerun the review triplet on the worker's returned `conclusion`. Stop after a bounded number of fix passes and report remaining blockers honestly.

If review exits `done with advisory surfaced`, treat that exit as a candidate for an affirmative success claim rather than the claim itself when `## Termination Gate` applies, and route the candidate through that gate before reporting success. Include any non-blocking advisory feedback without inlining logs.

If review exits `explicit user decision or another bounded review pass`, either run one more bounded pass with a concrete next iteration question tied to `GoalArtifact`, or ask the user to decide. Do not loop indefinitely.

After any explicit correction, use the existing correction gate to ask whether the goal or harness changed and whether evidence overturned the direction; emit exactly one concrete `continue`, `revise`, `stop`, or `escalate` action and name its responsible party before further work.

Every bounded pass in this skill - `meta-layer convergence`, a repeated review pass, fix passes, and termination-gate passes - shares one budget that defaults to at most five passes unless the user explicitly authorizes more, and the chosen bound is recorded before the first such pass.

## Termination Gate

`## Termination Gate` is a conditional subgate reached inside `fix_or_done`, never an additional `InlineConsensusProtocol` stage. It applies only when `## Goal Contract` supplies its positive, boundary-owner-confirmed `harness.provided_capabilities` entry and the caller is about to assert that `GoalArtifact` is satisfied. The gate permits only that `GoalArtifact`-scoped claim; it does not certify any broader host goal condition.

The gate binds that claim wherever it appears: in a final report, in a `done with advisory surfaced` outcome used as success, or in a `stop` gate action carrying the claim. It does not bind an `abstain` exit, `escalate`, a `stop` action that reports a blocker rather than achievement, or any exit for which `## Goal Contract` makes the gate inapplicable; those non-achievement exits keep their existing routing and must never be relabelled as goal satisfaction. `## Goal Contract` solely owns missing or invalid trigger-entry routing; this gate does not restate it.

This gate grants no authority over the host mechanism: it must not end, extend, replace, probe, discover, infer, clear, or implement that mechanism. It adds only the duty not to assert satisfaction without termination evidence; whether host continuation ends remains host-owned.

Dispatch exactly three purpose-built, independent, context-isolated termination seats. Their dispatch and completion use `WorkerDelegationContract`, `## Result Envelope`, `## Worker Completion Contract`, `## No Context Pollution`, and `## Reasoning Discipline` by reference:

- `criterion-evidence`: map every `normalized_goal` clause, constraint, and `success_criteria` item to current evidence. Absence of evidence is never satisfaction.
- `residual-gap`: adversarially falsify termination by answering the existing `iteration_question` with one concrete remaining difference from `GoalArtifact`, and name the responsible party for it. It must not broaden into a generic improvement search. The named difference must pass `DecisionGrounding`; an ungrounded worry is not a remaining difference.
- `claim-integrity`: reject a review exit, verdict count, caller narrative, host-provided capability, or lifecycle milestone as proxy evidence that a `GoalArtifact` obligation is discharged; also check whether any remaining obligation belongs to an owner declared in `harness.decision_ownership`.

Each termination seat returns one of:

- `satisfied`
- `unsatisfied`
- `abstain`

A termination seat returns a judgment, never a routing action. Termination flights use the existing `worker_flights` block with `SshxWorkerFlightRecord.stage` set to `termination`.

## Termination Truth Table

The meta-judge applies this fixed termination truth table in the caller context, exactly as it applies the design and review tables. The rows are evaluated in this order and are complete and, under this evaluation order, unambiguous:

| Inputs | Exit |
|---|---|
| caller judgment, a review exit, or any roster other than exactly the three distinct named isolated termination seats presented as termination consensus | `reject fake termination consensus` |
| unanimous `satisfied` | `termination claim permitted` |
| any `unsatisfied` | `withhold claim; continue against the named goal gap` |
| no `unsatisfied` and any `abstain`, invalid or missing seat result | `withhold claim; escalate with the unresolved evidence gap` |

For this table, unanimous `satisfied` means one valid `satisfied` result from each of the exactly three distinct named termination seats. Flight exhaustion is not an additional table input: after the existing retry and fallback path in `## Worker Delegation`, the table evaluates only the resulting seat roster and results, so a fallback-recovered result is treated like any other valid result.

Roster means the dispatch-time recorded named role identities: a named role absent from the roster reaches the first row, while a named role present without a valid result remains in the roster and reaches the fourth row unless an earlier row matches.

The meta-judge has no termination verdict of its own and must not convert `unsatisfied`, `abstain`, or missing or invalid worker output into permission to claim success. Each termination seat applies `DecisionGrounding` itself before returning; it is never a meta-judge downgrade path here, and no valid returned `unsatisfied` that passed the seat's check may be converted into permission by calling it ungrounded. A caller judgment or review exit is never termination consensus, and only the exact distinct named termination-seat roster can be presented as such.

The `withhold claim; continue against the named goal gap` exit routes that gap according to `harness.decision_ownership`. Only a work-target engineering correction assigned to the existing engineering path re-enters the review-`fix` path in `## Fix Or Done`, and its required rerun review triplet must finish before any new termination candidate. Only caller-owned orchestration remains with the authorized caller, and only new evidence from that owner may form a later candidate. A maintainer-owned product, governance, or boundary gap stops and escalates; any later routing requires a maintainer-authorized correction under `## Goal Contract`. Any gap whose declared owner does not match a route above stops and escalates to that declared owner; absent, ambiguous, or otherwise invalid ownership stops and escalates with the unresolved ownership gap.

Failure withholds the affirmative claim; it is not authority to keep working indefinitely, and a carrier outage must not become an unbounded work generator. A withheld claim reports honestly under the existing `abstain` discipline, while the host retains ownership of whether its continuation ends.

The gate may reach a completed result at most once per candidate affirmative termination and never gates its own exit. Every roster evaluation, including one that exits `reject fake termination consensus`, consumes exactly one unit of the shared bounded-pass budget in `## Fix Or Done` and creates no nested budget. A presentation rejected as fake termination consensus is not a completed gate run and may be corrected only while that shared budget remains. A later candidate is permitted only after new evidence or an authorized correction. At the ceiling, report the unresolved blocker and do not certify satisfaction.

## Boundaries

This skill is a prompt contract with a closed set of exactly four named mechanical script exceptions, governed only by `skills/sshx/CODEX_WORKER_SPEC.md` and their behavior tests:

- `skills/sshx/scripts/run-codex-worker.sh`;
- `skills/sshx/scripts/run-codex-worker-batch.sh`;
- `skills/sshx/scripts/read-codex-worker-status.sh`;
- `skills/sshx/scripts/clean-codex-worker-runs.sh`.

It must not add or depend on:

- any other helper script;
- daemons;
- repository-owned CLI;
- GitHub lifecycle operations;
- git lifecycle operations;
- labels;
- release authority;
- a public marker family;
- runtime host configuration as a production source of truth;
- other skills' or repository-owned internal prompts, scripts, or runtimes as an implementation dependency.

Allowed worker carriers are limited to `codex-cli`, `nyxid-oracle`, and `isolated-token-subagent`. Use them only as worker delegation capability, not as controller authority. `nyxid oracle` is used only as the `nyxid-oracle` worker carrier — a reasoning channel in the same category as `codex-cli` — never as a helper script the skill owns, a daemon, or a lifecycle actor.

## Baseline Failure Mode

Without this skill, lightweight high-risk decisions tend to regress to:

- prompt-only self-application where worker reasoning lives in the caller context;
- transcript-based pseudo-isolation presented as enough for independent workers;
- single-threaded advice presented as enough for consensus;
- no required worker mode declaration for peer perspectives;
- no fixed thinking truth table;
- no same-shape review gate before done;
- caller self-certification that a goal is satisfied inside a declared continuation context, with no isolated termination seat behind the terminal claim;
- asserting current-system facts without verifying actual evidence;
- silently relying on assumed factual premises instead of marking, verifying, gap-routing, or abstaining;
- judging only whether a plan is beautiful while never asking whether it is worth its cost, or over-building a beautiful form the goal does not need;
- treating an imagined adversary, consumer, caller, or input path as an established premise and defending against it;
- building defenses, validation, abstraction, or compatibility paths for a consumer no current call site or `GoalArtifact` term requires;
- rabbit-holing into local detail that no `GoalArtifact` term reaches and letting it block done;
- overstating carrier or model-family differences as evidence of independent priors or improved consensus quality;
- callers improvising per-run shell for worker fan-out, terminal status collection, and artifact deletion outside any source-owned specification or behavior test;
- pressure to use daemon, GitHub, or git orchestration for cases that only need inline consensus.

## Transcript Template

Use this compact transcript shape when the decision is non-trivial:

```text
intake:
  goal:
    raw_user_input:
    normalized_goal:
    constraints:
    success_criteria:
    iteration_question:
    harness:
      provided_capabilities:
      trust_boundary:
      decision_ownership:
    revisions:
      - change:
        authorization_source:
        invalidated_completed_work:
  strict_peer_invisibility_required:
worker_delegation:
  worker_mode_gate:
    codex_cli_capability_check:
    resolved_before_any_worker_dispatch:
    delegated_intake_context_gathering_allowed:
    fallback_reason:
  reason:
worker_flights:
  - flight_id:
    stage:
    role:
    worker_mode:
    worker_carrier:
    work_target:
    status:
    retry_budget:
    attempt:
    result_envelope_ref:
    completion_sentinel_ref:
thinking_panel_workers:
  - role: teleology
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: parsimony
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: fidelity
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: natural-ownership
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: proportional-containment
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: worth
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
meta_judge:
  exit:
  concrete_plan:
  goal_gap:
  next_iteration_question:
  focused_round:
  finding_downgrades:
  conclusion: # for an implement exit, includes the free-form ASCII relationship diagram (no separate diagram field)
  log_ref:
implementation_worker:
  worker_mode:
  worker_flight_ref:
  conclusion:
  log_ref:
review_triplet_workers:
  - role: architecture
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: quality
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: tests
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
fix_or_done:
  exit:
  termination_gate:
    applies:
    continuation_declaration_ref: # `GoalArtifact.harness.provided_capabilities`
    seats:
      - role: criterion-evidence
        bias:
        visible_inputs:
        worker_mode:
        worker_carrier:
        worker_flight_ref:
        verdict:
        conclusion:
        log_ref:
      - role: residual-gap
        bias:
        visible_inputs:
        worker_mode:
        worker_carrier:
        worker_flight_ref:
        verdict:
        conclusion:
        log_ref:
      - role: claim-integrity
        bias:
        visible_inputs:
        worker_mode:
        worker_carrier:
        worker_flight_ref:
        verdict:
        conclusion:
        log_ref:
    meta_judge:
      exit:
      goal_gap:
      next_iteration_question:
      responsible_party:
      conclusion:
      log_ref:
  conclusion:
  log_ref:
```

## Verification

The contract for this skill is verified by `skills/sshx/tests/test_sshx_contract.py`.

Before adding or changing this skill, record the no-skill failure mode as source-owned contract or test evidence. Do not track runtime artifacts as published skill source.

Before publishing or changing a claim about an external carrier or tool capability, verify the exact composed workflow end to end with the real tool. Fake carriers may supplement deterministic contract tests but must not be the sole evidence for a supported capability; when real verification is unavailable, mark the claim ASSUMED-UNVERIFIED and do not expose it as a supported option.
