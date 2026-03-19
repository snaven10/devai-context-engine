;; Functions
(function_declaration
  name: (identifier) @function.name
  parameters: (parameter_list) @function.params) @function.def

;; Methods
(method_declaration
  name: (field_identifier) @method.name
  parameters: (parameter_list) @method.params) @method.def

;; Structs
(type_declaration
  (type_spec
    name: (type_identifier) @struct.name
    type: (struct_type))) @struct.def

;; Interfaces
(type_declaration
  (type_spec
    name: (type_identifier) @interface.name
    type: (interface_type))) @interface.def
