# AI Novel

## 快速启动

### 生产模式
```bash
docker compose up -d --build
```
访问 http://localhost:8080

### 开发模式
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```
- 前端：http://localhost:5173
- 后端：http://localhost:3000

## 环境要求
- Docker 24+
- docker compose v2