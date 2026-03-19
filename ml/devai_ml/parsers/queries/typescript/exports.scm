;; export function foo
(export_statement
  (function_declaration
    name: (identifier) @function.name))

;; export class Foo
(export_statement
  (class_declaration
    name: (type_identifier) @class.name))

;; export default
(export_statement
  (identifier) @default.name)
