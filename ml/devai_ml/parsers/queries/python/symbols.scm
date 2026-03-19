;; Top-level functions
(function_definition
  name: (identifier) @function.name
  parameters: (parameters) @function.params
  return_type: (type)? @function.return_type) @function.def

;; Classes
(class_definition
  name: (identifier) @class.name
  superclasses: (argument_list)? @class.bases) @class.def

;; Methods inside classes
(class_definition
  body: (block
    (function_definition
      name: (identifier) @method.name
      parameters: (parameters) @method.params
      return_type: (type)? @method.return_type) @method.def))
