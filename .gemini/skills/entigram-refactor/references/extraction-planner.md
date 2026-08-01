# Extraction Planner Checklist

## Extraction Refactoring Targets
- [ ] **Extract Function**: Break down long methods (>30-40 lines) into focused, pure helper functions
- [ ] **Extract Class / Module**: Move secondary responsibilities into dedicated classes or helper modules
- [ ] **Extract Interface / Base Strategy**: Convert complex `if/elif/switch` chains into Strategy patterns
- [ ] **Extract Parameter Object**: Group >4 arguments into structured dataclasses, interfaces, or config objects
- [ ] **Replace Magic Constants with Enums/Constants**: Extract hardcoded strings/numbers into named constants

## Proposal Standard Format
For each proposed extraction, provide:
1. **Rationale & Benefits**: Clear explanation of why this extraction improves clarity or maintainability
2. **Target File / Path**: Exact file path where extracted code should reside
3. **Before Code Snippet**: Existing code highlighting the target block
4. **After Code Snippet**: Refactored code showing the extracted component and simplified callsite
5. **Impact & Effort Rating**:
   - Impact: High / Medium / Low
   - Effort: Low / Medium / High
