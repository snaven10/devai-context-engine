;; function()
(call_expression
  function: (identifier) @call.name)

;; path::function()
(call_expression
  function: (scoped_identifier
    name: (identifier) @call.name))

;; object.method()
(call_expression
  function: (field_expression
    field: (field_identifier) @call.name))
