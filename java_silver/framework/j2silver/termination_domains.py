"""Exact built-in domains inserted by the installed termination frontend.

These are frontend-generated declarations, not LLM-authored axioms. The proof
JSON has no domain field; changed or unfamiliar declarations remain rejected.
"""
GENERATED_DOMAINS = {
    'IntWellFoundedOrder': '''domain IntWellFoundedOrder  {
  
  axiom integer_ax_dec {
    (forall int1: Int, int2: Int ::
      { (decreasing(int1, int2): Bool) }
      int1 < int2 ==> (decreasing(int1, int2): Bool))
  }
  
  axiom integer_ax_bound {
    (forall int1: Int ::
      { (bounded(int1): Bool) }
      int1 >= 0 ==> (bounded(int1): Bool))
  }
}''',
    'WellFoundedOrder': '''domain WellFoundedOrder[T]  {
  
  function decreasing(arg1: T, arg2: T): Bool 
  
  function bounded(arg1: T): Bool 
}'''
}
