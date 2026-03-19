;; import { foo } from 'module'
(import_statement
  source: (string) @import.module) @import.def

;; import foo from 'module'
(import_statement
  (import_clause
    (identifier) @import.name)
  source: (string) @import.module) @import.def
