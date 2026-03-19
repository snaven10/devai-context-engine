;; Direct function calls: foo()
(call
  function: (identifier) @call.name)

;; Method calls: obj.method()
(call
  function: (attribute
    attribute: (identifier) @call.name))
