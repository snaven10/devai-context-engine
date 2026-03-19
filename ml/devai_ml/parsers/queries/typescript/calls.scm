;; foo()
(call_expression
  function: (identifier) @call.name)

;; obj.method()
(call_expression
  function: (member_expression
    property: (property_identifier) @call.name))
