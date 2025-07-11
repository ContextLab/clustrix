# Real-World Testing Documentation

## Overview

This document describes Clustrix's approach to real-world testing, where we validate functionality against actual external resources rather than relying solely on mock objects.

## Philosophy

Traditional testing often relies heavily on mocks and stubs to isolate code from external dependencies. While this approach has merit for unit testing, it can miss integration issues and doesn't validate that our code works correctly with real external systems.

Our real-world testing approach:
- **Validates actual functionality** against real external resources
- **Catches integration issues** that mocks can't detect
- **Ensures API compatibility** with actual service responses
- **Tests real-world performance** characteristics
- **Maintains cost control** through careful resource management

## Test Categories

### 1. Unit Tests (Existing)
- Fast execution (< 1 second)
- No external dependencies
- Use mocks for isolation
- Located in `tests/test_*.py`

### 2. Real-World Integration Tests (New)
- Test against actual external resources
- Validate API compatibility
- Test file system operations
- Located in `tests/real_world/`

### 3. Hybrid Tests (New)
- Combine real operations with mocks
- Use real resources for primary validation
- Use mocks for edge cases and error conditions
- Located in `tests/test_*_hybrid.py`

## Directory Structure

```
tests/
├── real_world/
│   ├── __init__.py              # Test infrastructure
│   ├── conftest.py              # Pytest configuration
│   ├── test_filesystem_real.py  # Real filesystem tests
│   ├── test_ssh_real.py         # Real SSH tests
│   ├── test_cloud_apis_real.py  # Real cloud API tests
│   ├── test_visual_verification.py # Widget visual tests
│   └── screenshots/             # Visual verification outputs
├── test_*_hybrid.py             # Hybrid tests
└── test_*.py                    # Traditional unit tests
```

## Test Infrastructure

### RealWorldTestManager

Manages resources and cost control for real-world tests:

```python
from tests.real_world import test_manager

# Check if we can make an API call
if test_manager.can_make_api_call(estimated_cost=0.01):
    # Make API call
    result = api_call()
    test_manager.record_api_call(cost=0.01)
```

### TempResourceManager

Manages temporary resources during testing:

```python
from tests.real_world import TempResourceManager

with TempResourceManager() as temp_mgr:
    # Create temporary files and directories
    temp_file = temp_mgr.create_temp_file("content", ".txt")
    temp_dir = temp_mgr.create_temp_dir()
    
    # Use resources
    # Automatic cleanup when exiting context
```

### TestCredentials

Manages credentials from environment variables:

```python
from tests.real_world import credentials

# Get AWS credentials
aws_creds = credentials.get_aws_credentials()
if aws_creds:
    # Use credentials
    pass
```

## Environment Variables

Set these environment variables to enable real-world tests:

### AWS Testing
```bash
export TEST_AWS_ACCESS_KEY="your-access-key"
export TEST_AWS_SECRET_KEY="your-secret-key"
export TEST_AWS_REGION="us-east-1"
```

### Azure Testing
```bash
export TEST_AZURE_SUBSCRIPTION_ID="your-subscription-id"
export TEST_AZURE_TENANT_ID="your-tenant-id"
export TEST_AZURE_CLIENT_ID="your-client-id"
export TEST_AZURE_CLIENT_SECRET="your-client-secret"
```

### GCP Testing
```bash
export TEST_GCP_PROJECT_ID="your-project-id"
export TEST_GCP_SERVICE_ACCOUNT_PATH="/path/to/service-account.json"
```

### SSH Testing
```bash
export TEST_SSH_HOST="localhost"
export TEST_SSH_USERNAME="$USER"
export TEST_SSH_PRIVATE_KEY_PATH="$HOME/.ssh/id_rsa"
```

## Running Tests

### Basic Test Execution

```bash
# Run all tests (excludes expensive and visual tests by default)
pytest

# Run only unit tests
pytest -m "unit"

# Run integration tests
pytest -m "integration"

# Run real-world tests
pytest -m "real_world"
```

### Advanced Test Execution

```bash
# Run expensive tests (may incur API costs)
pytest --run-expensive

# Run visual tests (require manual verification)
pytest --run-visual

# Set cost limits
pytest --api-cost-limit=10.0 --api-call-limit=200

# Run specific test categories
pytest -m "aws_required"
pytest -m "ssh_required"
```

### Test Selection Examples

```bash
# Run filesystem tests only
pytest tests/real_world/test_filesystem_real.py

# Run SSH tests with specific host
TEST_SSH_HOST=my-cluster.edu pytest tests/real_world/test_ssh_real.py

# Run hybrid tests
pytest tests/test_*_hybrid.py

# Run visual verification tests
pytest tests/real_world/test_visual_verification.py --run-visual
```

## Cost Management

### API Cost Limits

Real-world tests implement cost controls:

- **Daily API call limit**: 100 calls by default
- **Cost limit**: $5.00 USD by default
- **Free operations prioritized**: Use free-tier APIs when possible

### Cost-Conscious API Selection

We prioritize free or low-cost operations:

1. **AWS**: STS GetCallerIdentity, Pricing API (free)
2. **Azure**: List subscriptions, resource groups (free)
3. **GCP**: List zones, instances (free if no instances)
4. **Public APIs**: GitHub, PyPI, HuggingFace (free)

### Monitoring Costs

```python
# Check current cost status
from tests.real_world import test_manager

print(f"API calls today: {test_manager.api_calls_today}")
print(f"Current cost: ${test_manager.current_cost:.2f}")
print(f"Cost limit: ${test_manager.cost_limit_usd:.2f}")
```

## Test Types by Component

### 1. Filesystem Operations

**Real-world tests:**
- Create actual files and directories
- Test file permissions and ownership
- Verify cross-platform compatibility
- Test large file handling

**Hybrid tests:**
- Use real files for primary validation
- Mock SSH/SFTP for remote operations
- Mock error conditions (permissions, network failures)

### 2. SSH Operations

**Real-world tests:**
- Connect to actual SSH servers (localhost by default)
- Test key-based authentication
- Verify SFTP file operations
- Test connection timeouts and failures

**Hybrid tests:**
- Use real SSH for basic operations
- Mock for testing error conditions
- Mock for testing different server responses

### 3. Cloud Provider APIs

**Real-world tests:**
- Authenticate with actual cloud providers
- Call free-tier APIs
- Verify response formats
- Test error handling

**Hybrid tests:**
- Use real APIs for authentication
- Mock expensive operations
- Mock for testing service failures

### 4. Visual Verification

**Real-world tests:**
- Generate actual widget HTML
- Save screenshots for manual verification
- Test responsive design
- Verify accessibility features

## Best Practices

### 1. Test Organization

```python
class TestComponentReal:
    """Real-world tests for component functionality."""
    
    def test_basic_functionality_real(self):
        """Test basic functionality with real resources."""
        pass
    
    @pytest.mark.expensive
    def test_expensive_operation_real(self):
        """Test expensive operations."""
        pass
    
    @pytest.mark.visual
    def test_visual_verification(self):
        """Test visual components."""
        pass
```

### 2. Resource Management

```python
def test_with_cleanup():
    """Test with proper resource cleanup."""
    with TempResourceManager() as temp_mgr:
        # Create temporary resources
        temp_file = temp_mgr.create_temp_file("content")
        
        # Use resources
        # Automatic cleanup
```

### 3. Error Handling

```python
def test_with_error_handling():
    """Test with proper error handling."""
    try:
        # Test operation
        result = api_call()
        assert result is not None
    except Exception as e:
        # Skip if external service unavailable
        pytest.skip(f"External service unavailable: {e}")
```

### 4. Conditional Testing

```python
@pytest.mark.aws_required
def test_aws_functionality(aws_credentials):
    """Test AWS functionality if credentials available."""
    if not aws_credentials:
        pytest.skip("AWS credentials not available")
    
    # Test AWS operations
```

## Visual Verification

### Widget Testing

Visual tests generate HTML files for manual verification:

```python
def test_widget_visual():
    """Test widget visual appearance."""
    widget = create_widget()
    html = widget._repr_html_()
    
    # Save for manual verification
    with open("screenshots/widget.html", "w") as f:
        f.write(html)
```

### Manual Verification Process

1. **Run visual tests**: `pytest --run-visual`
2. **Open generated HTML files** in `tests/real_world/screenshots/`
3. **Compare with mockups** and design specifications
4. **Test responsive behavior** by resizing browser window
5. **Check accessibility** with screen reader tools
6. **Take screenshots** for documentation

### Screenshot Organization

```
tests/real_world/screenshots/
├── index.html                    # Test results index
├── modern_widget_output.html     # Modern widget HTML
├── enhanced_widget_output.html   # Enhanced widget HTML
├── widget_accessibility_report.html
├── widget_responsive_report.html
├── widget_comparison_report.html
└── performance_plots.png
```

## Continuous Integration

### GitHub Actions Integration

```yaml
name: Real-World Tests
on: [push, pull_request]

jobs:
  real-world-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: 3.9
      
      - name: Install dependencies
        run: |
          pip install -e ".[test]"
      
      - name: Run real-world tests
        env:
          TEST_AWS_ACCESS_KEY: ${{ secrets.TEST_AWS_ACCESS_KEY }}
          TEST_AWS_SECRET_KEY: ${{ secrets.TEST_AWS_SECRET_KEY }}
        run: |
          pytest tests/real_world/ -m "not expensive"
```

### Local Development

```bash
# Set up development environment
pip install -e ".[test]"

# Run fast tests during development
pytest -m "unit"

# Run integration tests before committing
pytest -m "integration"

# Run full test suite before release
pytest --run-expensive --run-visual
```

## Troubleshooting

### Common Issues

1. **Credential Errors**
   - Verify environment variables are set
   - Check credential validity
   - Ensure proper permissions

2. **SSH Connection Failures**
   - Verify SSH server is running
   - Check SSH key permissions
   - Test SSH connection manually

3. **API Rate Limits**
   - Check cost limits: `--api-cost-limit=X`
   - Verify daily limits: `--api-call-limit=X`
   - Use free-tier operations when possible

4. **File Permission Errors**
   - Ensure test directories are writable
   - Check temp directory permissions
   - Verify cleanup processes

### Debug Mode

```bash
# Run with verbose output
pytest -v -s

# Run with debug logging
pytest --log-cli-level=DEBUG

# Run single test for debugging
pytest tests/real_world/test_filesystem_real.py::TestRealFilesystemOperations::test_create_and_read_file_real -v -s
```

## Contributing

### Adding New Real-World Tests

1. **Create test file** in `tests/real_world/`
2. **Add appropriate markers** (`@pytest.mark.real_world`)
3. **Include cost estimates** for API operations
4. **Add environment variable documentation**
5. **Include cleanup procedures**

### Test File Template

```python
"""
Real-world tests for [component].

These tests use actual [external resource] to verify functionality.
"""

import pytest
from tests.real_world import test_manager, TempResourceManager

class TestComponentReal:
    """Real-world tests for component."""
    
    def test_basic_functionality_real(self):
        """Test basic functionality with real resources."""
        if not test_manager.can_make_api_call(0.01):
            pytest.skip("API limit reached")
        
        with TempResourceManager() as temp_mgr:
            # Test implementation
            pass
            
        test_manager.record_api_call(0.01)
```

## Metrics and Reporting

### Test Coverage

Real-world tests provide different coverage metrics:

- **API compatibility coverage**: % of APIs tested with real calls
- **Integration coverage**: % of integrations tested end-to-end
- **Visual coverage**: % of UI components visually verified
- **Platform coverage**: % of platforms tested

### Success Metrics

We track:
- **Test execution time**: Should remain reasonable
- **Cost per test run**: Should stay within budget
- **Real-world failure rate**: Should be low
- **Bug detection rate**: Should catch issues mocks miss

## Future Enhancements

### Planned Improvements

1. **Automated screenshot comparison**
2. **Performance benchmarking**
3. **Cross-platform CI testing**
4. **Integration with monitoring tools**
5. **Advanced cost optimization**

### Research Areas

- **Container-based testing environments**
- **Distributed test execution**
- **AI-powered visual verification**
- **Automated test generation**

---

This documentation provides a comprehensive guide to real-world testing in Clustrix. For questions or contributions, please refer to the main project documentation or open an issue on GitHub.