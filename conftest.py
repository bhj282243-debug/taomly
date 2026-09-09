# conftest.py — project root
# Makes all fixtures from tests/conftest.py available to modules/*/tests/.
# tests/__init__.py exists → "tests" is a package → this import is valid.
pytest_plugins = ["tests.conftest"]
