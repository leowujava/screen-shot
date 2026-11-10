## Fast Development & Testing Policy

### 1. Task Classification
Before implementation, classify task risk:

- P0: Comments, logs, formatting.
- P1: DTO, VO, simple CRUD.
- P2: Business logic, Mapper, SQL.
- P3: Cross-module changes, public components.
- P4: Authentication, transactions, security, architecture.

### 2. Testing Rules

- P0: No tests required.
- P1: Compile and run relevant tests.
- P2: Run affected unit tests.
- P3: Run affected unit and integration tests.
- P4: Run comprehensive regression tests.

### 3. Test Optimization

- Never run the entire test suite after every small modification.
- Identify affected modules and tests before execution.
- Reuse previous successful test results when relevant code
  and dependencies have not changed.
- Do not restart Spring Boot unnecessarily.
- Do not repeat tests that have already passed without
  relevant code changes.
- Run integration tests only when integration behavior changes.
- Run full regression before merging or releasing.

### 4. TDD Optimization

- Preserve RED-GREEN-REFACTOR for new business logic.
- Verify RED with the smallest relevant test.
- Verify GREEN with the affected test suite.
- Avoid redundant full-suite execution during refactoring.
- Never fabricate test results.

### 5. Review Optimization

- Use executing-plans for ordinary business tasks.
- Use subagent-driven-development for high-risk tasks.
- Perform one consolidated final code review.
- Escalate critical issues instead of repeatedly reviewing
  unchanged code.

### 6. Completion Criteria

Report:
- Modified files
- Tests executed
- Tests passed / failed
- Tests skipped and reasons
- Remaining risks