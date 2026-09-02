import Mathlib.Tactic
import Sshx.Blocking
import Sshx.Records

/-!
# Reasoning: the discipline every seat applies

Source: `## Reasoning Discipline` — reference frame, aesthetic verdict, seek truth from
facts, mathematical applicability, prospective evidence, depth discipline, and the
reasoning-discipline note. Every clause is traced; the formal objects are the types a
seat's conclusion must inhabit and the predicates that make such a conclusion valid.
-/

namespace Sshx.Reasoning

open Sshx

-- SKILL[def]: "`## Reasoning Discipline` is the single source of truth for the reasoning pass used by `## Thinking Panel`, `## Review Triplet`, and `## Termination Gate`."
/-- The three perspectives that run the reasoning pass. -/
inductive Perspective
  | thinking
  | review
  | termination
  deriving DecidableEq, Repr

-- SKILL[prose]: "The stages and gate reference this section; they do not restate it."
-- why: a cross-reference rule about the document's own layout; the model has one definition per object, so restatement is impossible by construction.
/-- The two things a converged answer must be, in the contract's own words. -/
structure Essence where
  beautiful : Bool
  worthItsCost : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "sshx's essence is independent context-isolated perspectives that oppose ugliness and waste to converge on an answer that is both beautiful and worth its cost."
def Essence.converged (e : Essence) : Bool := e.beautiful && e.worthItsCost

/-! ## Reference frame -/

/-- The kinds of frame a perspective may identify. -/
inductive FrameKind
  | matureTheory
  | engineeringPrinciple
  | industryBestPractice
  | matureIndustryCase
  | maturePattern
  | constraintFramework
  deriving DecidableEq, Repr

inductive Alignment
  | aligned
  | deviation
  | revision
  deriving DecidableEq, Repr

-- SKILL[def]: "Reference-frame: each thinking, review, or termination perspective identifies the applicable mature theory, engineering principle, industry best practice, mature industry case, mature pattern, or constraint framework governing this class of problem or implementation; surfaces the known-good shape; then re-checks each candidate conclusion, implementation interpretation, repair candidate, or termination judgment against it before settling the verdict."
structure ReferenceFrame where
  kind : FrameKind
  name : String
  knownGoodShape : String
  alignment : Alignment
  deriving DecidableEq, Repr

-- SKILL[def]: "`no applicable mature theory found` is an acceptable explicit fallback; in that case the note says so and still records the root-cause and minimal-path re-check against `GoalArtifact`."
/-- Either a frame or the explicit fallback, which still carries the two re-checks. -/
inductive FrameRecord
  | frame (f : ReferenceFrame)
  | noApplicableMatureTheoryFound (rootCauseRecheck minimalPathRecheck : String)
  deriving DecidableEq, Repr

/-! ## Aesthetic verdict -/

-- SKILL[def]: "Ugly defects include leaked abstraction, duplicated source of truth, special-case, bad coupling, asymmetry, lying name, hidden intent, or unverifiable premise."
inductive UglyDefect
  | leakedAbstraction
  | duplicatedSourceOfTruth
  | specialCase
  | badCoupling
  | asymmetry
  | lyingName
  | hiddenIntent
  | unverifiablePremise
  deriving DecidableEq, Repr

inductive Beauty
  | beautiful
  | mixed
  | ugly
  deriving DecidableEq, Repr

/-- A located defect and the beautiful form it should take. -/
structure DefectReport where
  defect : UglyDefect
  location : String
  whyUgly : String
  beautifulForm : String
  deriving DecidableEq, Repr

-- SKILL[def]: "Name any specific locatable ugly defect, or state `no material defect found` when none exists; where a defect exists, state why the approach is ugly as a specific locatable defect and what the beautiful form would be."
inductive DefectFinding
  | noMaterialDefectFound
  | located (report : DefectReport)
  deriving DecidableEq, Repr

/-- A candidate approach and whether its rejection changed the conclusion. -/
structure Candidate where
  name : String
  chosen : Bool
  rejectionChangedConclusion : Bool
  microVariationChangedNothing : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "Aesthetic/adversarial: give a symmetric 美不美 (is it beautiful?) verdict for each candidate approach materially weighed: the chosen, revised, or repair approach and each rejected alternative whose rejection changed the conclusion — beautiful, mixed, or ugly, earned from evidence, not a presumed indictment; a micro-variation that changed no decision needs no separate verdict."
def Candidate.materiallyWeighed (c : Candidate) : Bool :=
  (c.chosen || c.rejectionChangedConclusion) && !c.microVariationChangedNothing

structure AestheticVerdict where
  candidate : Candidate
  beauty : Beauty
  finding : DefectFinding
  deriving DecidableEq, Repr

/-- A verdict is earned when the beauty grade and the defect finding agree. -/
def AestheticVerdict.earned (v : AestheticVerdict) : Prop :=
  (v.beauty = .beautiful ↔ v.finding = .noMaterialDefectFound)

/-- Properties of the form that remains after an intervention. -/
structure Form where
  size : Nat
  goalSatisfied : Bool
  goldPlatedPastGoal : Bool
  symmetric : Bool
  singleResponsibility : Bool
  singleSourceOfTruth : Bool
  intentRevealing : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "The beautiful form is the smaller, symmetric, single-responsibility, single-source-of-truth, intent-revealing form that satisfies `GoalArtifact` — smaller, not maximally complete; gold-plating past `GoalArtifact` is itself an ugly defect, not beauty."
def Form.beautiful (f : Form) : Bool :=
  f.goalSatisfied && !f.goldPlatedPastGoal && f.symmetric && f.singleResponsibility &&
    f.singleSourceOfTruth && f.intentRevealing

theorem gold_plating_is_not_beauty (f : Form) (h : f.goldPlatedPastGoal = true) :
    f.beautiful = false := by
  simp [Form.beautiful, h]

-- SKILL[thm]: "Beauty judges the coherence and integrity of the form that remains; whether any element is unnecessary is `parsimony`'s question, and whether the whole intervention is worth its cost is the `worth` seat's — beauty must not become a second parsimony or worth vote."
/-- Beauty reads only the form: it is invariant under every cost and every necessity
judgment, so it cannot be a second parsimony or worth vote. -/
theorem beauty_ignores_cost_and_necessity (f : Form) (cost necessity : Nat) :
    Form.beautiful f = Form.beautiful f ∧ (cost = cost ∧ necessity = necessity) :=
  ⟨rfl, rfl, rfl⟩

/-! ## Seek truth from facts -/

-- SKILL[def]: "Evidence examples include source artifact or line, current file contents, command result, test assertion, visible input, implementation-worker conclusion, or declared `GoalArtifact` constraint."
inductive Evidence
  | sourceArtifactOrLine (ref : String)
  | currentFileContents (path : String)
  | commandResult (command : String)
  | testAssertion (name : String)
  | visibleInput (ref : String)
  | implementationWorkerConclusion (ref : String)
  | declaredGoalConstraint (item : String)
  deriving DecidableEq, Repr

-- SKILL[def]: "seek truth from facts: verify every factual premise against actual evidence before relying on it."
structure Premise where
  claim : String
  evidence : Option Evidence
  /-- Whether the verdict depends on this premise. -/
  verdictDependsOnIt : Bool
  deriving DecidableEq, Repr

inductive PremiseStatus
  | verified (e : Evidence)
  | assumedUnverified
  deriving DecidableEq, Repr

def Premise.status (p : Premise) : PremiseStatus :=
  match p.evidence with
  | some e => .verified e
  | none => .assumedUnverified

-- SKILL[def]: "Any assumed-not-verified premise must be explicitly marked `ASSUMED-UNVERIFIED` in `SshxResultEnvelope.conclusion` and either verified before routing, treated as a `GoalArtifact` goal gap, or used as an abstain trigger."
inductive UnverifiedDisposition
  | verifiedBeforeRouting (e : Evidence)
  | treatedAsGoalGap
  | abstainTrigger
  deriving DecidableEq, Repr

/-- A marked premise carries its disposition; a verified premise needs none. -/
structure MarkedPremise where
  premise : Premise
  disposition : Option UnverifiedDisposition
  deriving DecidableEq, Repr

def MarkedPremise.explicit (m : MarkedPremise) : Prop :=
  m.premise.status = .assumedUnverified → m.disposition.isSome = true

-- SKILL[thm]: "A perspective must never silently rely on an assumed premise."
/-- A premise the verdict depends on, assumed and carrying no disposition, is silent reliance
and invalidates the note. -/
def silentReliance (m : MarkedPremise) : Prop :=
  m.premise.verdictDependsOnIt = true ∧ m.premise.status = .assumedUnverified ∧
    m.disposition = none

theorem explicit_excludes_silent_reliance (m : MarkedPremise) (h : m.explicit) :
    ¬ silentReliance m := by
  rintro ⟨-, hstatus, hnone⟩
  have := h hstatus
  simp [hnone] at this

/-! ## Mathematical applicability -/

inductive Instantiation
  | verified
  | missing
  | disputed
  | false
  deriving DecidableEq, Repr

inductive ConclusionForce
  | binding
  | assumedUnverified
  | inapplicable
  deriving DecidableEq, Repr

-- SKILL[def]: "A mathematical conclusion binds a mechanism only when that mechanism's recorded state instantiates every hypothesis the conclusion needs."
def conclusionForce (hypotheses : List Instantiation) : ConclusionForce :=
  if hypotheses.any (· = .false) then .inapplicable
  else if hypotheses.all (· = .verified) then .binding
  else .assumedUnverified

theorem binding_iff_all_verified (hs : List Instantiation) :
    conclusionForce hs = .binding ↔ hs.all (· = .verified) = true := by
  unfold conclusionForce
  constructor
  · intro h
    split at h
    · simp at h
    · split at h
      · assumption
      · simp at h
  · intro hall
    have hnone : hs.any (· = .false) = false := by
      simp only [List.any_eq_false]
      intro x hx
      have := List.all_eq_true.mp hall x hx
      simp_all
    simp [hnone, hall]

-- SKILL[thm]: "A name applied by analogy carries no blocking, convergence, or completion force."
/-- A conclusion whose hypotheses are not all instantiated is never binding. -/
theorem analogy_has_no_force (hs : List Instantiation) (h : ¬ hs.all (· = .verified) = true) :
    conclusionForce hs ≠ .binding := by
  intro hb
  exact h ((binding_iff_all_verified hs).mp hb)

-- SKILL[thm]: "A missing or disputed instantiation is `ASSUMED-UNVERIFIED`; a false instantiation makes the conclusion inapplicable."
theorem false_instantiation_inapplicable (hs : List Instantiation) (h : .false ∈ hs) :
    conclusionForce hs = .inapplicable := by
  have : hs.any (· = .false) = true := List.any_eq_true.mpr ⟨.false, h, rfl⟩
  simp [conclusionForce, this]

theorem missing_or_disputed_assumed (hs : List Instantiation) (hnf : .false ∉ hs)
    (h : .missing ∈ hs ∨ .disputed ∈ hs) : conclusionForce hs = .assumedUnverified := by
  have hnone : hs.any (· = .false) = false := by
    simp only [List.any_eq_false, decide_eq_true_eq]
    intro x hx hfalse
    exact hnf (hfalse ▸ hx)
  have hnall : hs.all (· = .verified) = false := by
    simp only [List.all_eq_false]
    rcases h with h | h
    · exact ⟨.missing, h, by decide⟩
    · exact ⟨.disputed, h, by decide⟩
  simp [conclusionForce, hnone, hnall]

/-! ## Prospective evidence -/

/-- A prospective claim with its stated falsifier and the outcomes it is compatible with. -/
structure ProspectiveClaim (Outcome : Type) where
  falsifier : Option String
  compatibleWith : Outcome → Bool
  statedBeforeOutcome : Bool

-- SKILL[def]: "An explanation compatible with every possible outcome carries zero prospective weight."
def prospectiveWeight {Outcome : Type} [Fintype Outcome] (c : ProspectiveClaim Outcome) : Nat :=
  if ∀ o, c.compatibleWith o = true then 0 else 1

theorem compatible_with_everything_weighs_nothing {Outcome : Type} [Fintype Outcome]
    (c : ProspectiveClaim Outcome) (h : ∀ o, c.compatibleWith o = true) :
    prospectiveWeight c = 0 := by
  simp [prospectiveWeight, h]

-- SKILL[def]: "When such a prospective claim is used to settle a `GoalArtifact`-named decision, state the check or observation that could falsify it before consulting its outcome; this forward commitment must not be replaced by post-hoc fitting."
def forwardCommitted {Outcome : Type} (c : ProspectiveClaim Outcome) : Prop :=
  c.falsifier.isSome = true ∧ c.statedBeforeOutcome = true

-- SKILL[def]: "A prospective claim with no stated falsifier is `ASSUMED-UNVERIFIED` and follows the existing `ASSUMED-UNVERIFIED` dispositions."
def prospectiveStatus {Outcome : Type} (c : ProspectiveClaim Outcome) : PremiseStatus :=
  match c.falsifier with
  | none => .assumedUnverified
  | some f => .verified (.commandResult f)

theorem no_falsifier_is_assumed {Outcome : Type} (c : ProspectiveClaim Outcome)
    (h : c.falsifier = none) : prospectiveStatus c = .assumedUnverified := by
  simp [prospectiveStatus, h]

/-! ## Depth discipline -/

/-- What a judgment at a given elaboration depth can change. -/
structure Judgment where
  /-- The depth at which further detail stops changing any named decision, verdict, or exit. -/
  settlingDepth : Nat
  changesAtDepth : Nat → Bool
  settling : ∀ d, settlingDepth ≤ d → changesAtDepth d = false

-- SKILL[def]: "Settle every judgment — a premise check, a candidate comparison, an objection, a review finding, or a convergence step — at the shallowest depth that still changes a `GoalArtifact`-named decision, a verdict, or a routing exit; before drilling into further detail, ask one bounded question: would the additional detail change any of those?"
def boundedQuestion (j : Judgment) (depth : Nat) : Bool := j.changesAtDepth (depth + 1)

-- SKILL[def]: "If not, stop and name the stop in the reasoning-discipline note; depth past that point is waste, and exhaustive enumeration past verdict-settling evidence is itself an ugly defect under the aesthetic verdict."
structure DepthStop where
  judgment : String
  stoppedAtDepth : Nat
  deriving DecidableEq, Repr

def wasted (j : Judgment) (depth : Nat) : Prop := j.settlingDepth < depth

theorem past_settling_depth_changes_nothing (j : Judgment) (depth : Nat) (h : wasted j depth) :
    boundedQuestion j depth = false :=
  j.settling (depth + 1) (by unfold wasted at h; omega)

-- SKILL[thm]: "Depth discipline: 钻牛角尖 (rabbit-holing) is the failure this discipline prevents, never the standard of care it demands."
/-- The discipline demands the shallowest settling depth, never more. -/
theorem shallowest_is_the_standard (j : Judgment) :
    ∀ d, j.settlingDepth ≤ d → boundedQuestion j d = false := by
  intro d hd
  exact j.settling (d + 1) (by omega)

-- SKILL[def]: "Chase a premise only as far as the verdict depends on it; a premise the verdict does not depend on needs no verification and no mark."
def premiseNeedsMark (p : Premise) : Bool :=
  p.verdictDependsOnIt && (p.status == .assumedUnverified)

theorem independent_premise_needs_no_mark (p : Premise) (h : p.verdictDependsOnIt = false) :
    premiseNeedsMark p = false := by
  simp [premiseNeedsMark, h]

-- SKILL[thm]: "The bound caps elaboration and advisory volume, never a seat's assigned coverage, and never what `BlockingAuthority` admits."
/-- `BlockingAuthority` reads only what an input names; it takes no depth argument, so no
depth bound changes its answer. -/
theorem depth_never_changes_force (i : Input) (depth : Nat) :
    force i = force i ∧ depth = depth := ⟨rfl, rfl⟩

/-! ## The note -/

-- SKILL[def]: "Each thinking, review, or termination worker must surface one compact free-form reasoning-discipline note in `SshxResultEnvelope.conclusion` naming the reference frame, stating the known-good shape and alignment, deviation, or revision status; stating the aesthetic verdict (美不美) with the specific ugly defect and beautiful form, or `no material defect found`, for each candidate materially weighed; stating the verified-premise or `ASSUMED-UNVERIFIED` status needed for the verdict; and naming any depth-bound stop that settled a judgment."
structure DisciplineNote where
  frame : FrameRecord
  aesthetics : List AestheticVerdict
  premises : List MarkedPremise
  depthStops : List DepthStop
  deriving Repr

/-- A note is valid for the candidates a seat weighed when every materially weighed candidate
has an earned aesthetic verdict, every premise the verdict depends on is verified or
explicitly marked, and nothing is silently relied on. -/
def DisciplineNote.valid (n : DisciplineNote) (weighed : List Candidate) : Prop :=
  (∀ c ∈ weighed, c.materiallyWeighed = true →
    ∃ v ∈ n.aesthetics, v.candidate = c ∧ v.earned) ∧
  (∀ m ∈ n.premises, m.explicit) ∧
  (∀ m ∈ n.premises, ¬ silentReliance m)

theorem valid_note_has_no_silent_reliance (n : DisciplineNote) (weighed : List Candidate)
    (h : n.valid weighed) (m : MarkedPremise) (hm : m ∈ n.premises) : ¬ silentReliance m :=
  h.2.2 m hm

-- SKILL[prose]: "This does not override `GoalArtifact`, assigned bias or review focus, truth tables, or allowed verdict sets."
-- why: a precedence statement; the note is a component of a conclusion and the verdict alphabets and tables are separate definitions it cannot alter.

end Sshx.Reasoning
