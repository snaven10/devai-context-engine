;; Classes
(class_declaration
  name: (identifier) @class.name) @class.def

;; Interfaces
(interface_declaration
  name: (identifier) @interface.name) @interface.def

;; Methods
(method_declaration
  name: (identifier) @method.name
  parameters: (formal_parameters) @method.params) @method.def

;; Constructors
(constructor_declaration
  name: (identifier) @method.name
  parameters: (formal_parameters) @method.params) @method.def

;; Enums
(enum_declaration
  name: (identifier) @enum.name) @enum.def
