/-!
# Verdict alphabets

Source: `## Thinking Panel`, `## Review Triplet`, `## Termination Gate`, and the
fail-closed classes named by row 4 of `## Termination Truth Table`.
Each alphabet is closed; `mem_univ` certifies the enumeration is complete so every
`decide` over `univ` below is a proof over the whole type.
-/

namespace Sshx

-- SKILL: "Each seat returns one of:"
/-- `## Thinking Panel`: each seat returns exactly one of these. -/
inductive ThinkingVerdict
  | propose
  | revise
  | reject
  | abstain
  deriving DecidableEq, Repr

-- SKILL: "Each reviewer returns one of:"
/-- `## Review Triplet`: each reviewer returns exactly one of these. -/
inductive ReviewVerdict
  | approve
  | comment
  | reject
  deriving DecidableEq, Repr

-- SKILL: "Each termination seat returns one of:"
/-- `## Termination Gate` seat result as the termination truth table sees it.
`invalid` and `missing` are the fail-closed classes of row 4; `missing` is a named
role present in the roster without a valid result. -/
inductive TerminationSeat
  | satisfied
  | unsatisfied
  | abstain
  | invalid
  | missing
  deriving DecidableEq, Repr

def ThinkingVerdict.univ : List ThinkingVerdict := [.propose, .revise, .reject, .abstain]
def ReviewVerdict.univ : List ReviewVerdict := [.approve, .comment, .reject]
def TerminationSeat.univ : List TerminationSeat :=
  [.satisfied, .unsatisfied, .abstain, .invalid, .missing]

theorem ThinkingVerdict.mem_univ (v : ThinkingVerdict) : v ∈ ThinkingVerdict.univ := by
  cases v <;> decide

theorem ReviewVerdict.mem_univ (v : ReviewVerdict) : v ∈ ReviewVerdict.univ := by
  cases v <;> decide

theorem TerminationSeat.mem_univ (v : TerminationSeat) : v ∈ TerminationSeat.univ := by
  cases v <;> decide

end Sshx
