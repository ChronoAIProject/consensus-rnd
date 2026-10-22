/-!
# Carriers, worker mode, and fallback selection

Source: `## Worker Delegation` (`WorkerMode` priority list, fallback rule) and
`## Boundaries` (closed carrier set).
-/

set_option maxRecDepth 8192
set_option maxHeartbeats 800000

namespace Sshx

/-- Interpreted opt-in source in existing run/gate evidence, not a runtime setting.
The truth and run applicability of explicit user/host authority are premises. -/
inductive HecaOptIn
  | disabled
  | user
  | host
  deriving DecidableEq, Repr

-- SKILL[def]: "`heca` defaults off: requires explicit user/host opt-in for this run AND exposed heca MCP agent tools (lazy-listed included)."
-- SKILL[def]: "Silence/mention/heca context/tools alone never opt in."
structure HecaCapability where
  callerToolsExposed : Bool
  optIn : HecaOptIn := .disabled
  deriving DecidableEq, Repr

def HecaCapability.detected (facts : HecaCapability) : Bool :=
  facts.optIn != .disabled && facts.callerToolsExposed

/-- The closed carrier set of `## Boundaries`. -/
inductive Carrier
  | codexCli
  | heca
  | nyxidOracle
  | isolatedTokenSubagent
  deriving DecidableEq, Repr

-- SKILL[def]: "`WorkerMode` has exactly these values, in priority order:"
/-- `WorkerMode` priority order: a lower number is tried first. -/
def Carrier.priority : Carrier → Nat
  | .codexCli => 0
  | .heca => 1
  | .nyxidOracle => 2
  | .isolatedTokenSubagent => 3

/-- Carriers in priority order. -/
def Carrier.univ : List Carrier := [.codexCli, .heca, .nyxidOracle, .isolatedTokenSubagent]

theorem Carrier.mem_univ (c : Carrier) : c ∈ Carrier.univ := by
  cases c <;> decide

-- SKILL[def]: "A `tests` review seat must be assigned to a carrier capable of executing repository verification commands in the `work_target`, which is the per-seat constraint that keeps `nyxid-oracle` out of that seat's feasible draws."
def canRunRepositoryCommands : Carrier → Bool
  | .codexCli | .heca | .isolatedTokenSubagent => true
  | .nyxidOracle => false

/-- `WorkerMode` has exactly the four carriers plus `abstain`. -/
inductive WorkerMode
  | carrier (c : Carrier)
  | abstain
  deriving DecidableEq, Repr

/-- A per-carrier Boolean table; used both for "already tried for this stage and role"
and for "eligible for this stage and role" (available and role-permitted). -/
structure CarrierSet where
  codexCli : Bool
  heca : Bool
  nyxidOracle : Bool
  isolatedTokenSubagent : Bool
  deriving DecidableEq, Repr

def CarrierSet.get (s : CarrierSet) : Carrier → Bool
  | .codexCli => s.codexCli
  | .heca => s.heca
  | .nyxidOracle => s.nyxidOracle
  | .isolatedTokenSubagent => s.isolatedTokenSubagent

def CarrierSet.insert (s : CarrierSet) : Carrier → CarrierSet
  | .codexCli => { s with codexCli := true }
  | .heca => { s with heca := true }
  | .nyxidOracle => { s with nyxidOracle := true }
  | .isolatedTokenSubagent => { s with isolatedTokenSubagent := true }

def CarrierSet.empty : CarrierSet := ⟨false, false, false, false⟩

/-- Number of carriers not in the set. -/
def CarrierSet.remaining (s : CarrierSet) : Nat :=
  (if s.codexCli then 0 else 1) + (if s.heca then 0 else 1) + (if s.nyxidOracle then 0 else 1)
    + (if s.isolatedTokenSubagent then 0 else 1)

def CarrierSet.univ : List CarrierSet :=
  [⟨false, false, false, false⟩,
   ⟨false, false, false, true⟩,
   ⟨false, false, true, false⟩,
   ⟨false, false, true, true⟩,
   ⟨false, true, false, false⟩,
   ⟨false, true, false, true⟩,
   ⟨false, true, true, false⟩,
   ⟨false, true, true, true⟩,
   ⟨true, false, false, false⟩,
   ⟨true, false, false, true⟩,
   ⟨true, false, true, false⟩,
   ⟨true, false, true, true⟩,
   ⟨true, true, false, false⟩,
   ⟨true, true, false, true⟩,
   ⟨true, true, true, false⟩,
   ⟨true, true, true, true⟩]

theorem CarrierSet.mem_univ (s : CarrierSet) : s ∈ CarrierSet.univ := by
  cases s with
  | mk a b c d => cases a <;> cases b <;> cases c <;> cases d <;> decide

-- SKILL[def]: "reopen the assignment on the highest-priority eligible untried carrier from the full `WorkerMode` list"
/-- `## Worker Delegation` fallback rule: reopen the seat on the highest-priority eligible
carrier not yet tried for this stage and role, from the full `WorkerMode` list, never
"strictly downward from the failed carrier". -/
def nextCarrier (eligible tried : CarrierSet) : Option Carrier :=
  (Carrier.univ.filter (fun c => eligible.get c && !(tried.get c))).head?

/-- The chosen fallback carrier was never tried. -/
theorem nextCarrier_untried :
    ∀ e ∈ CarrierSet.univ, ∀ t ∈ CarrierSet.univ, ∀ c ∈ Carrier.univ,
      nextCarrier e t = some c → t.get c = false := by
  decide

/-- The chosen fallback carrier is eligible. -/
theorem nextCarrier_eligible :
    ∀ e ∈ CarrierSet.univ, ∀ t ∈ CarrierSet.univ, ∀ c ∈ Carrier.univ,
      nextCarrier e t = some c → e.get c = true := by
  decide

/-- The chosen fallback carrier has the highest priority among eligible untried carriers. -/
theorem nextCarrier_highest_priority :
    ∀ e ∈ CarrierSet.univ, ∀ t ∈ CarrierSet.univ, ∀ c ∈ Carrier.univ, ∀ d ∈ Carrier.univ,
      nextCarrier e t = some c → e.get d = true → t.get d = false → c.priority ≤ d.priority := by
  decide

/-- Marking an untried carrier strictly shrinks the untried remainder: the fallback chain
for one seat is bounded by the number of carriers. -/
theorem remaining_insert_lt :
    ∀ t ∈ CarrierSet.univ, ∀ c ∈ Carrier.univ,
      t.get c = false → (t.insert c).remaining < t.remaining := by
  decide

/-- The fallback chain a seat can traverse, with explicit fuel; `fuel` only guards
structural recursion and never changes the result once it exceeds the carrier count. -/
def fallbackChain (eligible : CarrierSet) : CarrierSet → Nat → List Carrier
  | _, 0 => []
  | tried, fuel + 1 =>
    match nextCarrier eligible tried with
    | none => []
    | some c => c :: fallbackChain eligible (tried.insert c) fuel

/-- At most four carriers are ever opened for one seat, whatever the eligibility. -/
theorem fallbackChain_length_le :
    ∀ e ∈ CarrierSet.univ, ∀ t ∈ CarrierSet.univ,
      (fallbackChain e t (Carrier.univ.length + 1)).length ≤ t.remaining := by
  decide

theorem fallbackChain_from_empty_le_carrier_count (e : CarrierSet) :
    (fallbackChain e CarrierSet.empty (Carrier.univ.length + 1)).length ≤ Carrier.univ.length :=
  fallbackChain_length_le e (CarrierSet.mem_univ e) CarrierSet.empty (by decide)

/-- The pre-heca selector, retained as an explicit correspondence target. -/
def legacyNextCarrier (eligible tried : CarrierSet) : Option Carrier :=
  ([.codexCli, .nyxidOracle, .isolatedTokenSubagent].filter
    (fun c => eligible.get c && !(tried.get c))).head?

theorem nextCarrier_heca_absent (e t : CarrierSet) (absent : e.heca = false) :
    nextCarrier e t = legacyNextCarrier e t := by
  cases e with
  | mk a b c d =>
    cases t with
    | mk w x y z =>
      cases a <;> cases b <;> cases c <;> cases d <;>
        cases w <;> cases x <;> cases y <;> cases z <;> simp_all [nextCarrier, legacyNextCarrier, Carrier.univ, CarrierSet.get]

def legacyFallbackChain (eligible : CarrierSet) : CarrierSet → Nat → List Carrier
  | _, 0 => []
  | tried, fuel + 1 =>
    match legacyNextCarrier eligible tried with
    | none => []
    | some c => c :: legacyFallbackChain eligible (tried.insert c) fuel

theorem fallback_heca_absent (e : CarrierSet) (absent : e.heca = false)
    (fuel : Nat) (t : CarrierSet) :
    fallbackChain e t fuel = legacyFallbackChain e t fuel := by
  induction fuel generalizing t with
  | zero => rfl
  | succ fuel ih =>
    simp only [fallbackChain, legacyFallbackChain, nextCarrier_heca_absent e t absent]
    cases legacyNextCarrier e t <;> simp [ih]

-- SKILL[def]: "Only when no eligible untried carrier remains or every fallback fails to produce terminal completion is the result `abstain`"
/-- Every seat ends in a carrier flight or `abstain`: when nothing eligible remains untried,
the only result is `abstain`. -/
def resolveSeat (eligible tried : CarrierSet) : WorkerMode :=
  match nextCarrier eligible tried with
  | some c => .carrier c
  | none => .abstain

theorem resolveSeat_abstain_iff (e t : CarrierSet) :
    resolveSeat e t = .abstain ↔ nextCarrier e t = none := by
  unfold resolveSeat
  cases nextCarrier e t <;> simp

end Sshx
