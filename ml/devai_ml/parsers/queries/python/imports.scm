;; import module
(import_statement
  name: (dotted_name) @import.module) @import.def

;; from module import names
(import_from_statement
  module_name: (dotted_name) @import.module
  name: (dotted_name) @import.name) @import.def
