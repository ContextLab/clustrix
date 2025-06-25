# Contributing to Clustrix

We welcome contributions to Clustrix! This document provides guidelines for contributing to the project.

## Development Setup

### Prerequisites

- Python 3.8 or higher
- Git

### Setting Up Development Environment

1. **Clone the repository:**
   ```bash
   git clone https://github.com/ContextLab/clustrix.git
   cd clustrix
   ```

2. **Install in development mode:**
   ```bash
   pip install -e ".[dev]"
   ```

3. **Install pre-commit hooks (optional but recommended):**
   ```bash
   pip install pre-commit
   pre-commit install
   ```

## Development Workflow

### Code Quality

Before submitting any code, please ensure it meets our quality standards:

1. **Format your code:**
   ```bash
   black clustrix/
   ```

2. **Run linting:**
   ```bash
   flake8 clustrix/
   ```

3. **Type checking:**
   ```bash
   mypy clustrix/
   ```

4. **Run tests:**
   ```bash
   pytest
   ```

### Testing

We maintain comprehensive test coverage. When contributing:

- **Write tests** for all new functionality
- **Update existing tests** when modifying behavior
- **Ensure all tests pass** before submitting

Run tests with coverage:
```bash
pytest --cov=clustrix --cov-report=html
```

### Code Style

- Follow **PEP 8** style guidelines
- Use **type hints** for all function parameters and return values
- Write **docstrings** for all public functions and classes
- Keep functions focused and small
- Use descriptive variable names

### Documentation

- Update docstrings for any modified functions
- Add examples for new features
- Update README.md if adding user-facing features
- Consider adding notebook tutorials for complex features

## Types of Contributions

### Bug Reports

When reporting bugs, please include:

- **Description** of the bug
- **Steps to reproduce** the issue
- **Expected behavior** vs actual behavior
- **Environment details** (Python version, OS, cluster type)
- **Error messages** or logs if applicable

### Feature Requests

For new features:

- **Describe the feature** and its use case
- **Explain why** it would be valuable
- **Provide examples** of how it would be used
- **Consider backwards compatibility**

### Code Contributions

#### Pull Request Process

1. **Create a feature branch:**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** following the development workflow

3. **Add tests** for your changes

4. **Update documentation** as needed

5. **Commit your changes:**
   ```bash
   git commit -m "Add feature: brief description"
   ```

6. **Push to your fork:**
   ```bash
   git push origin feature/your-feature-name
   ```

7. **Create a Pull Request** on GitHub

#### Pull Request Guidelines

- **One feature per PR** - keep changes focused
- **Write clear commit messages** describing what and why
- **Include tests** for all new functionality
- **Update documentation** for user-facing changes
- **Ensure CI passes** before requesting review
- **Respond to feedback** promptly and constructively

## Project Structure

```
clustrix/
├── clustrix/              # Main package
│   ├── __init__.py       # Package exports
│   ├── config.py         # Configuration management
│   ├── decorator.py      # @cluster decorator
│   ├── executor.py       # Remote cluster execution
│   ├── local_executor.py # Local parallel execution
│   ├── loop_analysis.py  # Loop detection and analysis
│   ├── cli.py           # Command-line interface
│   └── utils.py         # Utility functions
├── tests/               # Test suite
├── docs/               # Documentation
├── examples/           # Example scripts
└── setup.py           # Package configuration
```

## Areas for Contribution

We particularly welcome contributions in these areas:

### High Priority
- **SGE and Kubernetes support** - Complete the unfinished implementations
- **Enhanced loop parallelization** - Improve automatic loop detection
- **Error handling** - Robust error recovery and reporting
- **Performance optimization** - Reduce overhead and improve efficiency

### Medium Priority
- **Additional cluster types** - Support for other schedulers
- **Advanced configuration** - More flexible configuration options
- **Monitoring and logging** - Better observability
- **Documentation** - Tutorials, examples, and API docs

### Low Priority
- **GUI interface** - Web-based or desktop interface
- **Integration plugins** - Jupyter, IDEs, etc.
- **Cloud providers** - AWS Batch, Google Cloud, etc.

## Coding Standards

### Function Documentation

```python
def example_function(param1: str, param2: int = 10) -> bool:
    """
    Brief description of what the function does.
    
    Args:
        param1: Description of parameter 1
        param2: Description of parameter 2 with default value
        
    Returns:
        Description of return value
        
    Raises:
        ValueError: When param1 is empty
        ConnectionError: When cluster is unreachable
        
    Example:
        >>> result = example_function("test", 20)
        >>> print(result)
        True
    """
```

### Error Handling

- Use **specific exception types** rather than generic Exception
- **Log errors** appropriately (debug, info, warning, error)
- **Provide helpful error messages** with context
- **Clean up resources** in finally blocks or context managers

### Testing Guidelines

- **Test both success and failure cases**
- **Use mocks** for external dependencies (SSH, file system)
- **Test edge cases** and boundary conditions
- **Keep tests independent** - no shared state between tests
- **Use descriptive test names** that explain what is being tested

## Security Considerations

When contributing:

- **Never commit secrets** or credentials
- **Validate user inputs** to prevent injection attacks
- **Use safe evaluation** instead of `eval()` or `exec()`
- **Follow security best practices** for SSH and remote execution
- **Review dependencies** for known vulnerabilities

## Getting Help

- **GitHub Issues** - For bugs and feature requests
- **GitHub Discussions** - For questions and general discussion
- **Code review** - We provide constructive feedback on all PRs

## Recognition

Contributors will be:

- **Listed in CONTRIBUTORS.md**
- **Mentioned in release notes** for significant contributions
- **Credited in documentation** where appropriate

Thank you for contributing to Clustrix!