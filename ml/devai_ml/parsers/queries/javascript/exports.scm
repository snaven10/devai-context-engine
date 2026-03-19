(export_statement
  (function_declaration
    name: (identifier) @function.name))

(export_statement
  (class_declaration
    name: (identifier) @class.name))

(export_statement
  (identifier) @default.name)
