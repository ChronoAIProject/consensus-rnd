/-!
# Context isolation

Source: `## No Context Pollution` (hard invariant and append-only role ledger) and the
`visible_inputs` rule of `## InlineConsensusProtocol`.
-/

namespace Sshx

/-- A seat is identified by its round and its index within the round. -/
structure Seat where
  round : Nat
  index : Nat
  deriving DecidableEq, Repr

/-- Things a worker could in principle read. -/
inductive Artifact
  | goalArtifact
  | brief (s : Seat)
  | output (s : Seat)
  | ownPriorAttempt (s : Seat)
  | callerTranscript
  deriving DecidableEq, Repr

-- SKILL[def]: "no worker may see a same-round peer output or caller-conversation transcript content that was not explicitly included in its dispatch brief or `GoalArtifact`"
/-- What seat `w` may see: the complete `GoalArtifact`, its own brief, its own prior
attempt for the same seat, and conclusions of strictly earlier rounds that its brief
carries. Never a same-round peer output and never caller-conversation transcript content
that was not placed in its brief. -/
def visible (w : Seat) : Artifact → Bool
  | .goalArtifact => true
  | .brief s => s == w
  | .output s => decide (s.round < w.round)
  | .ownPriorAttempt s => s == w
  | .callerTranscript => false

theorem same_round_peer_invisible (w s : Seat) (h : s.round = w.round) :
    visible w (.output s) = false := by
  simp [visible, h]

theorem own_output_invisible (w : Seat) : visible w (.output w) = false := by
  simp [visible]

theorem transcript_invisible (w : Seat) : visible w .callerTranscript = false := rfl

theorem goal_always_visible (w : Seat) : visible w .goalArtifact = true := rfl

-- SKILL[def]: "An event may use only evidence in its recorded prefix."
/-- Append-only role ledger: an event may use only evidence in its recorded prefix. -/
def ledgerValid (events : List (Nat × List Nat)) : Bool :=
  events.all fun e => e.2.all fun used => used < e.1

theorem ledger_rejects_out_of_prefix (i : Nat) (used : List Nat) (u : Nat)
    (hu : u ∈ used) (h : i ≤ u) : ledgerValid [(i, used)] = false := by
  simp [ledgerValid]
  exact ⟨u, hu, h⟩

/-- Later appends cannot change a frozen prefix: validity of a prefix is preserved. -/
theorem ledger_prefix_stable (events tail : List (Nat × List Nat))
    (h : ledgerValid (events ++ tail) = true) : ledgerValid events = true := by
  simp [ledgerValid] at h ⊢
  exact h.1

end Sshx
