[中文](CONTRIBUTING_zh.md)

# Contributing Guide

Thank you for your interest in the AI Novel project!

## Setting Up the Development Environment

### Prerequisites

- Python 3.13+
- Node.js 20+
- [uv](https://docs.astral.sh/uv/) — Python package manager
- npm — Node package manager

### Start the Development Environment

```bash
# Clone the project
git clone https://github.com/hollis-openlab/ai-novel-rewriter.git && cd ai-novel-rewriter

# Start frontend and backend
./start.sh
```

Backend runs at `http://localhost:8899`, frontend at `http://localhost:5173`.

### Running Tests

```bash
# Backend tests
uv run pytest backend/tests/ -v

# Frontend tests
cd frontend && npm test
```

## Submitting Code

1. Fork the project and create your branch (`git checkout -b feature/xxx`)
2. Make sure tests pass (`uv run pytest backend/tests/ -v`)
3. Commit your changes (`git commit -m 'feat: xxx'`)
4. Push the branch (`git push origin feature/xxx`)
5. Create a Pull Request

### Commit Convention

Use [Conventional Commits](https://www.conventionalcommits.org/) format:

- `feat:` New feature
- `fix:` Bug fix
- `refactor:` Refactoring
- `docs:` Documentation
- `test:` Tests
- `chore:` Build/toolchain

### Notes

- Do not commit any files under the `data/` directory
- Do not commit `.env` files or API keys
- Ensure existing tests pass after modifying backend code
- New features should include tests
