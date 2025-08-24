---
name: Test Coverage Improvement
about: Track efforts to improve code coverage
title: '[COVERAGE] Improve test coverage for [module/feature]'
labels: 'testing, coverage'
assignees: ''

---

# Improve Test Coverage to 90%+

## Current Status
- **Overall Coverage**: 70% (up from 56%)
- **Target**: 90%+

## Completed Work
✅ Lambda Cloud Provider: 10% → 100%  
✅ GCP Provider: 12% → 96%  
✅ HuggingFace Spaces Provider: 15% → 97%  
✅ Azure Provider: 17% → 96%  

## Remaining Work

### 1. AWS Provider (Priority: High)
**Current Coverage**: 32% → **Target**: 90%+

**Issues to Fix**:
- ClientError mocking in tests needs proper exception handling
- Need comprehensive tests for:
  - VPC creation and management (`_create_or_get_vpc_for_eks`)
  - Subnet creation (`_create_eks_subnets`) 
  - Security group management (`_create_eks_security_groups`)
  - EKS cluster lifecycle (create, delete, status)
  - EC2 instance lifecycle (create, delete, status)
  - Node group management
  - Cost estimation with all instance types
  - Region and instance type retrieval
  - Cluster configuration generation
  - Error handling for all AWS API calls

**Technical Notes**:
- Fix ClientError mocking: Use `from clustrix.cloud_providers.aws import ClientError` instead of patching
- Add comprehensive edge case testing
- Test both EKS and EC2 cluster types
- Test authentication edge cases

### 2. Notebook Magic Module (Priority: High)
**Current Coverage**: 48% → **Target**: 90%+
**File**: `clustrix/notebook_magic.py` (1236 statements, 648 missing)

**Areas Needing Tests**:
- Jupyter magic command parsing
- Interactive widget functionality
- Configuration management
- Cloud provider integration
- Error handling for invalid configurations
- Magic command registration and execution

### 3. Cost Providers (Priority: Medium)
**Files and Current Coverage**:
- `clustrix/cost_providers/aws.py`: 75% 
- `clustrix/cost_providers/azure.py`: 54%
- `clustrix/cost_providers/gcp.py`: 60%
- `clustrix/cost_providers/lambda_cloud.py`: 65%

**Need comprehensive tests for**:
- Cost calculation algorithms
- API integration for real-time pricing
- Error handling for unavailable pricing data
- Rate limiting and caching

### 4. Core Modules (Priority: Medium-Low)
**Files needing improvement**:
- `clustrix/cloud_provider_manager.py`: 65%
- `clustrix/cost_monitoring.py`: 64% 
- `clustrix/loop_analysis.py`: 72%
- `clustrix/utils.py`: 70%
- `clustrix/executor.py`: 71%

## Implementation Strategy

### Test Structure
**Unit Tests** (`tests/unit/`): Fast, isolated tests with mocked dependencies
- Core functionality and business logic
- Run in CI/CD pipeline
- No external dependencies

**Integration Tests** (`tests/integration/`): Cross-module interaction tests
- Component integration testing
- Limited external dependencies
- Run in CI/CD pipeline

**Real-World Tests** (`tests/real_world/`): End-to-end testing with actual resources
- Live cluster and API testing
- Excluded from automated CI (credential requirements)
- Run via pre-push hook when credentials available

### Phase 1: AWS Provider Tests
1. Fix ClientError mocking issues in `tests/unit/`
2. Add comprehensive VPC/subnet/security group tests
3. Test EKS cluster lifecycle completely in `tests/integration/`
4. Add real AWS integration tests in `tests/real_world/`
5. Add edge case and error handling tests

### Phase 2: Notebook Magic
1. Mock Jupyter environment for testing in `tests/unit/`
2. Test magic command parsing and execution
3. Test widget configuration management in `tests/integration/`
4. Test cloud provider integration through widgets

### Phase 3: Cost Providers
1. Mock external pricing APIs in `tests/unit/`
2. Test cost calculation accuracy
3. Test error handling for API failures in `tests/integration/`
4. Test caching and rate limiting in `tests/real_world/`

### Phase 4: Core Modules
1. Identify specific uncovered lines using coverage reports
2. Add targeted unit tests for missing functionality
3. Focus on error handling and edge cases in appropriate test categories

## Success Criteria
- [ ] Overall test coverage ≥ 90%
- [ ] All cloud providers ≥ 95% coverage
- [ ] No critical functionality untested
- [ ] All edge cases and error conditions covered
- [ ] Tests are maintainable and reliable

## Technical Requirements
- Use proper mocking for external APIs
- Ensure tests are deterministic and fast
- Follow existing test patterns and conventions
- Add docstrings for all test methods
- Use meaningful test names that describe behavior