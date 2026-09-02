/-!
# Carriers, worker mode, and fallback selection

Source: `## Worker Delegation` (`WorkerMode` priority list, fallback rule) and
`## Boundaries` (closed carrier set).
-/

namespace Sshx

/-- The closed carrier set of `## Boundaries`. -/
inductive Carrier
  | codexCli
  | nyxidOracle
  | isolatedTokenSubagent
  deriving DecidableEq, Repr

-- SKILL[def]: "`WorkerMode` has exactly these values, in priority order:"
/-- `WorkerMode` priority order: a lower number is tried first. -/
def Carrier.priority : Carrier → Nat
  | .codexCli => 0
  | .nyxidOracle => 1
  | .isolatedTokenSubagent => 2

/-- Carriers in priority order. -/
def Carrier.univ : List Carrier := [.codexCli, .nyxidOracle, .isolatedTokenSubagent]

theorem Carrier.mem_univ (c : Carrier) : c ∈ Carrier.univ := by
  cases c <;> decide

/-- `WorkerMode` has exactly the three carriers plus `abstain`. -/
inductive WorkerMode
  | carrier (c : Carrier)
  | abstain
  deriving DecidableEq, Repr

/-- A per-carrier Boolean table; used both for "already tried for this stage and role"
and for "eligible for this stage and role" (available and role-permitted). -/
structure CarrierSet where
  codexCli : Bool
  nyxidOracle : Bool
  isolatedTokenSubagent : Bool
  deriving DecidableEq, Repr

def CarrierSet.get (s : CarrierSet) : Carrier → Bool
  | .codexCli => s.codexCli
  | .nyxidOracle => s.nyxidOracle
  | .isolatedTokenSubagent => s.isolatedTokenSubagent

def CarrierSet.insert (s : CarrierSet) : Carrier → CarrierSet
  | .codexCli => { s with codexCli := true }
  | .nyxidOracle => { s with nyxidOracle := true }
  | .isolatedTokenSubagent => { s with isolatedTokenSubagent := true }

def CarrierSet.empty : CarrierSet := ⟨false, false, false⟩

/-- Number of carriers not in the set. -/
def CarrierSet.remaining (s : CarrierSet) : Nat :=
  (if s.codexCli then 0 else 1) + (if s.nyxidOracle then 0 else 1)
    + (if s.isolatedTokenSubagent then 0 else 1)

def CarrierSet.univ : List CarrierSet :=
  [⟨false, false, false⟩, ⟨false, false, true⟩, ⟨false, true, false⟩, ⟨false, true, true⟩,
   ⟨true, false, false⟩, ⟨true, false, true⟩, ⟨true, true, false⟩, ⟨true, true, true⟩]

theorem CarrierSet.mem_univ (s : CarrierSet) : s ∈ CarrierSet.univ := by
  cases s with
  | mk a b c => cases a <;> cases b <;> cases c <;> decide

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

/-- At most three carriers are ever opened for one seat, whatever the eligibility. -/
theorem fallbackChain_length_le :
    ∀ e ∈ CarrierSet.univ, ∀ t ∈ CarrierSet.univ,
      (fallbackChain e t 4).length ≤ t.remaining := by
  decide

theorem fallbackChain_from_empty_le_three (e : CarrierSet) :
    (fallbackChain e CarrierSet.empty 4).length ≤ 3 :=
  fallbackChain_length_le e (CarrierSet.mem_univ e) CarrierSet.empty (by decide)

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
