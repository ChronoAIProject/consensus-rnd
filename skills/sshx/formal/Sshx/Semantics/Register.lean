import Mathlib.Data.Fintype.Basic
import Mathlib.SetTheory.Cardinal.Finite
import D5.S0.Diagonal.CaptureCount
import Sshx.Blocking

/-!
# Semantics: the advisory register is a Lawvere listing

Source: `## Reasoning Discipline`, the enumeration paragraph after `BlockingAuthority`.
Instance of the kernel-frozen escape theorems in `D5.S0.Diagonal.CaptureCount`.

The contract's register of advisory shapes is a listing `g : A → A → Force`: each named
shape `a` classifies every shape. The adversarial seat's charter is the diagonal
`fun a => flip (g a a)`. Because `flip` has no fixed point, the diagonal is never a row
of the register: every finite register is escaped, whatever it lists.
-/

namespace Sshx.Semantics

open D5.S0.Diagonal.EscapeCount D5.S0.Diagonal.CaptureCount

/-- The twist on the force alphabet: no force is its own flip. -/
def Force.flip : Sshx.Force → Sshx.Force
  | .blocking => .advisory
  | .advisory => .blocking

instance : Fintype Sshx.Force :=
  ⟨{.blocking, .advisory}, by intro x; cases x <;> simp⟩

theorem flip_fixfree : Nat.card {y : Sshx.Force // Force.flip y = y} = 0 := by
  haveI : IsEmpty {y : Sshx.Force // Force.flip y = y} :=
    ⟨fun ⟨y, hy⟩ => by cases y <;> simp [Force.flip] at hy⟩
  exact Nat.card_of_isEmpty

-- SKILL[thm]: "every finite listing of cases is escaped by a fixed-point-free self-application"
/-- Every finite register of advisory shapes is escaped by its own diagonal
(`escape_all_of_fixfree` instantiated at `Force` and `flip`). -/
theorem every_register_escaped {A : Type*} [Fintype A] (register : A → A → Sshx.Force) :
    IsEscaped Force.flip register :=
  escape_all_of_fixfree Force.flip flip_fixfree register

-- SKILL[thm]: "no extension of this or any register can complete it"
/-- The diagonal classification is never one of the register's rows: no extension of the
register lists it, because the escaped listings are all `2 ^ |A|²` listings
(`escaped_card_of_fixfree`). -/
theorem register_diagonal_unlisted {A : Type*} [Fintype A] (register : A → A → Sshx.Force) :
    diagonal Force.flip register ∉ Set.range register :=
  every_register_escaped register

theorem every_register_counted {A : Type*} [Fintype A] :
    Nat.card {g : A → A → Sshx.Force // IsEscaped Force.flip g} =
      2 ^ (Fintype.card A ^ 2) := by
  have h := escaped_card_of_fixfree (A := A) Force.flip flip_fixfree
  have hcard : Fintype.card Sshx.Force = 2 := rfl
  rw [hcard] at h
  exact h

end Sshx.Semantics
