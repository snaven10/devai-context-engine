;; Classes
(class_declaration
  name: (name) @class.name) @class.def

;; Interfaces
(interface_declaration
  name: (name) @interface.name) @interface.def

;; Traits
(trait_declaration
  name: (name) @trait.name) @trait.def

;; Methods
(method_declaration
  name: (name) @method.name
  parameters: (formal_parameters) @method.params) @method.def

;; Functions
(function_definition
  name: (name) @function.name
  parameters: (formal_parameters) @function.params) @function.def

;; Enums
(enum_declaration
  name: (name) @enum.name) @enum.def
