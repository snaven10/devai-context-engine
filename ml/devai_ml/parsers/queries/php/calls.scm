;; functionName()
(function_call_expression
  function: (name) @call.name)

;; Qualified\Name\func()
(function_call_expression
  function: (qualified_name) @call.name)

;; $object->method()
(member_call_expression
  name: (name) @call.name)

;; Class::staticMethod()
(scoped_call_expression
  name: (name) @call.name)
