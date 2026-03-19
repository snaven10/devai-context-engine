;; Single import
(import_declaration
  (import_spec
    path: (interpreted_string_literal) @import.module)) @import.def

;; Grouped imports
(import_declaration
  (import_spec_list
    (import_spec
      path: (interpreted_string_literal) @import.module) @import.def))
