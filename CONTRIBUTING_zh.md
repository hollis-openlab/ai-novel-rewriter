[English](CONTRIBUTING.md)

# 贡献指南

感谢你对 AI Novel 项目的兴趣！

## 开发环境搭建

### 前置要求

- Python 3.13+
- Node.js 20+
- [uv](https://docs.astral.sh/uv/) — Python 包管理
- npm — Node 包管理

### 启动开发环境

```bash
# 克隆项目
git clone <repo-url> && cd AI-novel

# 一键启动前后端
./start.sh
```

后端运行在 `http://localhost:8899`，前端运行在 `http://localhost:5173`。

### 运行测试

```bash
# 后端测试
uv run pytest backend/tests/ -v

# 前端测试
cd frontend && npm test
```

## 提交代码

1. Fork 项目并创建你的分支 (`git checkout -b feature/xxx`)
2. 确保测试通过 (`uv run pytest backend/tests/ -v`)
3. 提交变更 (`git commit -m 'feat: xxx'`)
4. 推送分支 (`git push origin feature/xxx`)
5. 创建 Pull Request

### Commit 规范

使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

- `feat:` 新功能
- `fix:` Bug 修复
- `refactor:` 重构
- `docs:` 文档
- `test:` 测试
- `chore:` 构建/工具链

### 注意事项

- 不要提交 `data/` 目录下的任何文件
- 不要提交 `.env` 文件或 API Key
- 改动后端代码后确保现有测试通过
- 新功能请附带测试
