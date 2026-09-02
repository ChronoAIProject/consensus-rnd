import Mathlib.Tactic
import Sshx.Behavior.Model

/-!
# Behavior: safety invariants over every reachable state

Each theorem is traced to the contract clause it certifies. Proofs go by induction on
`Reachable` and case analysis on the action taken.
-/

namespace Sshx.Behavior

open Sshx

/-! ## Actions that are never allowed -/

-- SKILL[inv]: "Caller-authored `&`, `nohup`, `disown`, and `setsid` remain forbidden."
theorem never_shell_background (s : ProtocolState) (id : Nat) :
    ¬ allowed s (.launchViaShellBackground id) := fun h => h

-- SKILL[inv]: "It must not monitor files or logs to poll for completion; doing so conflicts with the no-polling rule above."
theorem never_poll (s : ProtocolState) (id : Nat) : ¬ allowed s (.pollArtifacts id) := fun h => h

-- SKILL[inv]: "`sshx` must not discover or infer the goal from external lifecycle milestones, release state, runtime host configuration, GitHub issues, GitHub pull requests, labels, branches, or any other external lifecycle surface."
theorem never_lifecycle (s : ProtocolState) (op : LifecycleOp) : ¬ allowed s (.lifecycle op) :=
  fun h => h

-- SKILL[inv]: "When the needed content is not already public, the brief inlines it instead."
theorem never_publish_to_link (s : ProtocolState) : ¬ allowed s .publishToMakeLinkable :=
  fun h => h

-- SKILL[inv]: "Logs are not inline in caller context."
theorem never_carry_full_log (s : ProtocolState) (id : Nat) : ¬ allowed s (.carry (.fullLog id)) := by
  simp [allowed, guardCarry, ContextItem.permitted]

theorem never_carry_peer_output (s : ProtocolState) (id : Nat) :
    ¬ allowed s (.carry (.peerOutput id)) := by
  simp [allowed, guardCarry, ContextItem.permitted]

theorem never_carry_worker_reasoning (s : ProtocolState) (id : Nat) :
    ¬ allowed s (.carry (.workerReasoning id)) := by
  simp [allowed, guardCarry, ContextItem.permitted]

/-! ## Helper facts about effects -/

theorem updateFlight_mem {s : ProtocolState} {id : Nat} {g : FlightRec → FlightRec} {f : FlightRec}
    (h : f ∈ (updateFlight s id g).flights) :
    ∃ x ∈ s.flights, f = x ∨ f = g x := by
  simp only [updateFlight, List.mem_map] at h
  obtain ⟨x, hx, hfx⟩ := h
  refine ⟨x, hx, ?_⟩
  by_cases hid : (x.id == id) = true
  · right; simp [hid] at hfx; exact hfx.symm
  · left; simp [hid] at hfx; exact hfx.symm

theorem updateFlight_ne_nil {s : ProtocolState} {id : Nat} {g : FlightRec → FlightRec}
    (h : (updateFlight s id g).flights ≠ []) : s.flights ≠ [] := by
  intro hemp
  simp [updateFlight, hemp] at h

/-! ## Invariants -/

/-- Reachable states satisfy the conjunction below; the pieces are exported individually. -/
structure Safe (s : ProtocolState) : Prop where
  flightsNeedCarrierMode : s.flights ≠ [] → ∃ c, s.mode = some (.carrier c)
  modeNeedsGoal : s.mode.isSome = true → s.goal.isSome = true
  codexChecked : s.mode.isSome = true → Carrier.codexCli ∈ s.capabilityChecked
  refsOnlyTerminal : ∀ f ∈ s.flights,
    (f.envelopeRef.isSome = true ∨ f.sentinelRef.isSome = true) → f.status = .terminal
  attemptLeBudget : ∀ f ∈ s.flights, f.attempt ≤ f.retryBudget
  contextPermitted : ∀ item ∈ s.context, item.permitted = true
  noMutationWhileActive : ∀ entry ∈ s.mutationLog, entry.2 = false
  exitFromTable : ∀ e, s.terminationExit = some e →
    ∃ source roster, e = terminationRoute source roster

theorem safe_initial : Safe ProtocolState.initial := by
  constructor <;> simp [ProtocolState.initial]

/-- Flight-list facts survive `updateFlight` with a record transformer that keeps them. -/
theorem refs_attempt_updateFlight {s : ProtocolState} {id : Nat} {g : FlightRec → FlightRec}
    (hg : ∀ x ∈ s.flights,
      ((g x).envelopeRef.isSome = true ∨ (g x).sentinelRef.isSome = true) → (g x).status = .terminal)
    (hg' : ∀ x ∈ s.flights, (g x).attempt ≤ (g x).retryBudget)
    (h5 : ∀ f ∈ s.flights,
      (f.envelopeRef.isSome = true ∨ f.sentinelRef.isSome = true) → f.status = .terminal)
    (h6 : ∀ f ∈ s.flights, f.attempt ≤ f.retryBudget) :
    (∀ f ∈ (updateFlight s id g).flights,
      (f.envelopeRef.isSome = true ∨ f.sentinelRef.isSome = true) → f.status = .terminal) ∧
    (∀ f ∈ (updateFlight s id g).flights, f.attempt ≤ f.retryBudget) := by
  constructor
  · intro f hf
    obtain ⟨x, hx, hfx | hfx⟩ := updateFlight_mem hf
    · rw [hfx]; exact h5 x hx
    · rw [hfx]; exact hg x hx
  · intro f hf
    obtain ⟨x, hx, hfx | hfx⟩ := updateFlight_mem hf
    · rw [hfx]; exact h6 x hx
    · rw [hfx]; exact hg' x hx

theorem safe_step {s : ProtocolState} {a : Action} (hs : Safe s) (ha : allowed s a) :
    Safe (step s a) := by
  obtain ⟨h1, h2, h4, h5, h6, h7, h8, h9⟩ := hs
  cases a with
  | inspectReadOnly => exact ⟨h1, h2, h4, h5, h6, h7, h8, h9⟩
  | writeGoal g =>
    refine ⟨h1, ?_, h4, h5, h6, h7, h8, h9⟩
    intro _; simp [step]
  | appendRevision r =>
    refine ⟨h1, ?_, h4, h5, h6, h7, h8, h9⟩
    intro hm
    have := h2 hm
    simp only [step]
    cases hg : s.goal <;> simp_all
  | capabilityCheck c =>
    refine ⟨h1, h2, ?_, h5, h6, h7, h8, h9⟩
    intro hm; simp only [step, List.mem_cons]; exact Or.inr (h4 hm)
  | resolveMode m =>
    obtain ⟨hg, -, hnone, hcodex, hm⟩ := ha
    have hempty : s.flights = [] := by
      by_contra hne
      obtain ⟨c, hc⟩ := h1 hne
      simp [hnone] at hc
    refine ⟨?_, ?_, ?_, h5, h6, h7, h8, h9⟩
    · intro hne; simp [step] at hne; exact absurd hempty hne
    · intro _; simpa [step, ProtocolState.goalWritten] using hg
    · intro _; simpa [step] using hcodex
  | openFlight stage role carrier target retryBudget =>
    obtain ⟨hres, habs, -, -⟩ := ha
    refine ⟨?_, h2, h4, ?_, ?_, h7, h8, h9⟩
    · intro _
      cases hm : s.mode with
      | none => simp [ProtocolState.modeResolved, hm] at hres
      | some m =>
        cases m with
        | carrier c => exact ⟨c, by simp [step, hm]⟩
        | abstain => exact absurd hm (by simpa [ProtocolState.abstained] using habs)
    · intro f hf
      simp only [step, List.mem_append, List.mem_singleton] at hf
      rcases hf with hf | hf
      · exact h5 f hf
      · rw [hf]; simp [newFlight]
    · intro f hf
      simp only [step, List.mem_append, List.mem_singleton] at hf
      rcases hf with hf | hf
      · exact h6 f hf
      · rw [hf]; simp [newFlight]
  | launchViaRunner id =>
    have hu := refs_attempt_updateFlight (s := s) (id := id)
      (g := fun f => { f with launched := true })
      (fun x _ hr => by simpa using h5 x ‹_› (by simpa using hr))
      (fun x hx => by simpa using h6 x hx) h5 h6
    refine ⟨fun hne => h1 (updateFlight_ne_nil hne), h2, h4, hu.1, hu.2, h7, h8, h9⟩
  | launchViaShellBackground id => exact ha.elim
  | pollArtifacts id => exact ha.elim
  | hostNotified id =>
    have hu := refs_attempt_updateFlight (s := s) (id := id)
      (g := fun f => { f with notified := true })
      (fun x _ hr => by simpa using h5 x ‹_› (by simpa using hr))
      (fun x hx => by simpa using h6 x hx) h5 h6
    refine ⟨fun hne => h1 (updateFlight_ne_nil hne), h2, h4, hu.1, hu.2, h7, h8, h9⟩
  | collect id o =>
    have hu := refs_attempt_updateFlight (s := s) (id := id) (g := collectEffect o)
      (fun x _ hr => by
        simp only [collectEffect] at hr ⊢
        split
        · rfl
        · split <;> simp_all)
      (fun x hx => by
        simp only [collectEffect]
        split
        · simpa using h6 x hx
        · split
          · rename_i hlt; simpa using hlt
          · simpa using h6 x hx) h5 h6
    refine ⟨fun hne => h1 (updateFlight_ne_nil hne), h2, h4, hu.1, hu.2, h7, h8, h9⟩
  | fallbackFlight id carrier =>
    obtain ⟨f0, hf0, -, -, -⟩ := ha
    have hstep : step s (.fallbackFlight id carrier) =
        { s with flights := s.flights ++ [reopenFlight s.freshId carrier f0] } := by
      simp [step, hf0]
    rw [hstep]
    refine ⟨?_, h2, h4, ?_, ?_, h7, h8, h9⟩
    · intro _
      apply h1
      intro hemp
      simp [ProtocolState.flight, hemp] at hf0
    · intro f hf
      simp only [List.mem_append, List.mem_singleton] at hf
      rcases hf with hf | hf
      · exact h5 f hf
      · rw [hf]; simp [reopenFlight]
    · intro f hf
      simp only [List.mem_append, List.mem_singleton] at hf
      rcases hf with hf | hf
      · exact h6 f hf
      · rw [hf]; simp [reopenFlight]
  | mutateTarget target =>
    refine ⟨h1, h2, h4, h5, h6, h7, ?_, h9⟩
    intro entry he
    simp only [step, List.mem_cons] at he
    rcases he with he | he
    · rw [he]; exact ha
    · exact h8 entry he
  | carry item =>
    refine ⟨h1, h2, h4, h5, h6, ?_, h8, h9⟩
    intro i hi
    simp only [step, List.mem_cons] at hi
    rcases hi with hi | hi
    · rw [hi]; exact ha
    · exact h7 i hi
  | recordPassBudget units => exact ⟨h1, h2, h4, h5, h6, h7, h8, h9⟩
  | pass t => exact ⟨h1, h2, h4, h5, h6, h7, h8, h9⟩
  | advanceStage => exact ⟨h1, h2, h4, h5, h6, h7, h8, h9⟩
  | declareGate a => exact ⟨h1, h2, h4, h5, h6, h7, h8, h9⟩
  | evaluateTermination source roster =>
    refine ⟨h1, h2, h4, h5, h6, h7, h8, ?_⟩
    intro e he
    simp only [step, Option.some.injEq] at he
    exact ⟨source, roster, he.symm⟩
  | claimSatisfied => exact ⟨h1, h2, h4, h5, h6, h7, h8, h9⟩
  | lifecycle op => exact ha.elim
  | oracleReference url isPublic pinned => exact ⟨h1, h2, h4, h5, h6, h7, h8, h9⟩
  | publishToMakeLinkable => exact ha.elim

theorem safe_of_reachable {s : ProtocolState} (h : Reachable s) : Safe s := by
  induction h with
  | initial => exact safe_initial
  | move _ ha ih => exact safe_step ih ha

/-! ## Exported invariants, one per clause -/

-- SKILL[inv]: "When `WorkerMode` resolves to `abstain`, the protocol terminates at `choose_worker_mode`: the caller emits a final `SshxResultEnvelope` whose `conclusion` records the `abstain` verdict, the reason, and any options, creates no thinking, implementation, or review flight, and runs no later stage."
theorem abstain_has_no_flights {s : ProtocolState} (h : Reachable s) (ha : s.abstained) :
    s.flights = [] := by
  by_contra hne
  obtain ⟨c, hc⟩ := (safe_of_reachable h).flightsNeedCarrierMode hne
  simp [ProtocolState.abstained] at ha
  simp [ha] at hc

-- SKILL[inv]: "Thinking, implementation, review, and termination-gate work are worker dispatches."
theorem flights_only_after_mode {s : ProtocolState} (h : Reachable s) (hne : s.flights ≠ []) :
    ∃ c, s.mode = some (.carrier c) :=
  (safe_of_reachable h).flightsNeedCarrierMode hne

-- SKILL[inv]: "The caller must write and complete `harness` during `intake`, before any worker dispatch."
theorem goal_before_flights {s : ProtocolState} (h : Reachable s) (hne : s.flights ≠ []) :
    s.goal.isSome = true := by
  obtain ⟨c, hc⟩ := flights_only_after_mode h hne
  exact (safe_of_reachable h).modeNeedsGoal (by simp [hc])

-- SKILL[inv]: "`codex-cli` is an out-of-process worker carrier."
theorem codex_checked_before_mode {s : ProtocolState} (h : Reachable s) (hm : s.mode.isSome = true) :
    Carrier.codexCli ∈ s.capabilityChecked :=
  (safe_of_reachable h).codexChecked hm

-- SKILL[inv]: "`result_envelope_ref` and `completion_sentinel_ref` are empty until a terminal worker result exists."
theorem refs_only_when_terminal {s : ProtocolState} (h : Reachable s) (f : FlightRec)
    (hf : f ∈ s.flights) (hr : f.envelopeRef.isSome = true ∨ f.sentinelRef.isSome = true) :
    f.status = .terminal :=
  (safe_of_reachable h).refsOnlyTerminal f hf hr

-- SKILL[inv]: "`status` is one of `in-flight`, `retrying`, `terminal`, or `abstained`."
theorem attempt_le_retry_budget {s : ProtocolState} (h : Reachable s) (f : FlightRec)
    (hf : f ∈ s.flights) : f.attempt ≤ f.retryBudget :=
  (safe_of_reachable h).attemptLeBudget f hf

-- SKILL[inv]: "Final reports aggregate `conclusion` values only and retain `log_ref` references for optional inspection."
theorem context_only_permitted {s : ProtocolState} (h : Reachable s) (item : ContextItem)
    (hi : item ∈ s.context) : item.permitted = true :=
  (safe_of_reachable h).contextPermitted item hi

-- SKILL[inv]: "The caller must not take over the same `work_target` because a process snapshot, log text, or workspace state appears quiet."
theorem no_mutation_while_active {s : ProtocolState} (h : Reachable s) (entry : String × Bool)
    (he : entry ∈ s.mutationLog) : entry.2 = false :=
  (safe_of_reachable h).noMutationWhileActive entry he

-- SKILL[inv]: "The meta-judge applies this fixed termination truth table in the caller context, exactly as it applies the design and review tables."
theorem termination_exit_from_table {s : ProtocolState} (h : Reachable s) (e : TerminationExit)
    (he : s.terminationExit = some e) : ∃ source roster, e = terminationRoute source roster :=
  (safe_of_reachable h).exitFromTable e he

-- SKILL[inv]: "The gate permits only that `GoalArtifact`-scoped claim; it does not certify any broader host goal condition."
theorem claim_only_under_permitted_gate (s : ProtocolState) (ha : allowed s .claimSatisfied) :
    s.gate = .applies → s.terminationExit = some .claimPermitted :=
  ha.2.2

-- SKILL[inv]: "Because `pass_budget` is a strictly decreasing natural number, the run terminates: reaching zero reports every unresolved blocker honestly and is never evidence of method stop or goal completion."
theorem budget_never_increases (s : ProtocolState) (a : Action) (ha : allowed s a) (b b' : Nat)
    (hb : s.passBudget = some b) (hb' : (step s a).passBudget = some b') : b' ≤ b := by
  cases a
  case recordPassBudget units => obtain ⟨-, hnone⟩ := ha; simp [hnone] at hb
  case pass t =>
    simp only [step, hb, Option.bind_some] at hb'
    exact Sshx.step_le b t b' hb'
  case evaluateTermination source roster =>
    simp only [step, hb, Option.bind_some] at hb'
    exact Sshx.step_le b _ b' hb'
  case fallbackFlight id carrier =>
    simp only [step] at hb'
    split at hb' <;> simp_all
  all_goals simp_all [step, updateFlight]

/-- Stage index in protocol order. -/
def Stage.index : Stage → Nat
  | .intake => 0
  | .chooseWorkerMode => 1
  | .thinkingPanel => 2
  | .metaJudge => 3
  | .implementation => 4
  | .reviewTriplet => 5
  | .fixOrDone => 6

-- SKILL[inv]: "A thinking-stage exhaustion in particular skips `meta_judge`, `implementation_worker`, `review_triplet_workers`, and `fix_or_done`."
theorem stage_never_regresses (s : ProtocolState) (a : Action) :
    Stage.index s.stage ≤ Stage.index (step s a).stage := by
  cases a
  case advanceStage =>
    simp only [step]
    cases s.stage <;> simp [Stage.next, Stage.index]
  case fallbackFlight id carrier =>
    simp only [step]
    split <;> simp
  all_goals simp [step, updateFlight]

end Sshx.Behavior
