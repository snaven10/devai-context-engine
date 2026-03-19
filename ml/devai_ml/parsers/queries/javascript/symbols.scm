;; Functions
(function_declaration
  name: (identifier) @function.name
  parameters: (formal_parameters) @function.params) @function.def

;; Classes
(class_declaration
  name: (identifier) @class.name) @class.def

;; Methods
(method_definition
  name: (property_identifier) @method.name
  parameters: (formal_parameters) @method.params) @method.def

;; Arrow functions assigned to const/let/var
(lexical_declaration
  (variable_declarator
    name: (identifier) @function.name
    value: (arrow_function
      parameters: (formal_parameters) @function.params)) @function.def)
