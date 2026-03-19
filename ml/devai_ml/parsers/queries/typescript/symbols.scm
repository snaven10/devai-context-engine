;; Functions
(function_declaration
  name: (identifier) @function.name
  parameters: (formal_parameters) @function.params) @function.def

;; Classes
(class_declaration
  name: (type_identifier) @class.name) @class.def

;; Methods
(method_definition
  name: (property_identifier) @method.name
  parameters: (formal_parameters) @method.params) @method.def

;; Interfaces
(interface_declaration
  name: (type_identifier) @interface.name) @interface.def

;; Type aliases
(type_alias_declaration
  name: (type_identifier) @type.name) @type.def

;; Enums
(enum_declaration
  name: (identifier) @enum.name) @enum.def

;; Arrow functions assigned to const
(lexical_declaration
  (variable_declarator
    name: (identifier) @function.name
    value: (arrow_function
      parameters: (formal_parameters) @function.params)) @function.def)
