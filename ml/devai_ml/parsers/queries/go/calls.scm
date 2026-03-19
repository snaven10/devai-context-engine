;; foo()
(call_expression
  function: (identifier) @call.name)

;; pkg.Func()
(call_expression
  function: (selector_expression
    field: (field_identifier) @call.name))
