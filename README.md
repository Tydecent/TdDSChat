# chat-demo 云端后端（Flask + SQLite）

把 [chat-demo](https://github.com/deepseek-ai/chat-demo) 的浏览器本地存储换成服务端存储，并加上单用户登录。

- 会话 / 附件 / 设置：SQLite + 磁盘
- 认证：Flask session + werkzeug 密码哈希（单用户，无注册）
- API Key：只存服务端，前端 `GET /api/settings` 仅得到 `hasApiKey`
- 聊天请求：`/api/proxy/*` 反向代理到 DeepSeek，由服务端注入 Key

## 快速开始

```bash
cp .env.example .env

# 生成密码哈希，粘贴到 .env 的 CHAT_PASSWORD_HASH
python3 scripts/hash_password.py

./start.sh              # 或：python3 run.py
# 打开 http://127.0.0.1:5000
```

未配置 `CHAT_PASSWORD_HASH` / `CHAT_PASSWORD` 时，首次启动会生成一次性随机密码并打印到日志。

前端产物默认从 `server/public` 读取；若为空，先执行：

```bash
server/scripts/build_frontend.sh   # pnpm build + 同步 dist
```

## 目录

```
app.py            应用工厂：蓝图注册、登录页、静态资源、错误处理
config.py         环境变量配置（不包含任何密钥字面量）
db.py             SQLite 访问层（conversations / settings / files / users）
security.py       密码哈希、login_required、CSRF 同源校验、登录限流
validators.py     请求体校验（Session / 设置增量）
views/auth.py            /api/auth/*
views/conversations.py   /api/conversations/*
views/settings.py        /api/settings/*
views/files.py           /api/files/*
views/proxy.py           /api/proxy/*
login/            登录页（原生 HTML/CSS/JS）
public/           前端构建产物（由脚本同步）
scripts/          hash_password.py、build_frontend.sh
```

## 生产部署提示

```bash
pip install gunicorn
gunicorn -w 1 --threads 8 -b 0.0.0.0:5000 'app:create_app()'
```

- 必须 `-w 1`：SQLite 与内存态登录限流都是单进程假设。
- 反向代理需关闭对 `/api/proxy/` 的响应缓冲（nginx: `proxy_buffering off;`），否则流式回复会被攒包。
- 通过 HTTPS 访问时设置 `SESSION_COOKIE_SECURE=1`。

## 法律与许可