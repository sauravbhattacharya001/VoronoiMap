# Contributing

Thank you for your interest in contributing to VoronoiMap! Here's how to get started.

## Development Setup

```bash
# Clone the repository
git clone https://github.com/sauravbhattacharya001/VoronoiMap.git
cd VoronoiMap

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

## Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ -v --cov=vormap --cov-report=term-missing

# Run only specific test classes
python -m pytest tests/test_vormap.py -v -k "TestGetNN"
```

## Code Style

- Follow PEP 8 conventions
- Use descriptive variable names
- Add docstrings to all public functions
- Keep functions focused — one responsibility per function

## Making Changes

1. **Fork** the repository
2. Create a **feature branch** (`git checkout -b feature/my-feature`)
3. Make your changes with **tests**
4. Run the test suite (`python -m pytest tests/ -v`)
5. **Commit** with a descriptive message
6. **Push** to your fork (`git push origin feature/my-feature`)
7. Open a **Pull Request**

## What We're Looking For

- **Bug fixes** with regression tests
- **Performance improvements** with benchmarks
- **New estimation strategies** or oracle types
- **Documentation** improvements
- **Security** hardening

## Reporting Issues

When filing an issue, please include:

- Python version (`python --version`)
- Whether SciPy is installed
- Minimal reproduction steps
- Expected vs actual behavior
- Data file (if applicable and not sensitive)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
