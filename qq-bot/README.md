# 大肥鱼 · QQ 群机器人

一个只在被 @ 或叫名字时说话、用贴吧老哥语气聊天的群友。

## 它是 workflow，不是 agent

整条管线写死在 `bot.py` 里：

```
收到群消息 → 存进上下文 → 是不是叫我？ → 冷却到了吗？ → 拼人设+最近20条 → 调模型 → 发回群
```

每个判断都是 `if`，你能一眼看出它什么时候说话、说什么。
这不是缺点——**群聊机器人就该这样**，否则它会刷屏、乱插话、烧钱。

## 本地先试玩（不用 QQ）

```bash
cd /Users/hezhao/Workspace/Code/demo1
python3 qq-bot/bot.py --fake
```

打字回车看它怎么回。没配 key 只会说「典」「绷不住了」，先把链路跑通。

填好 `qq-bot/.env` 里的 `OPENAI_API_KEY`（DeepSeek 的 key），再跑一次就是真效果。

**人设不满意改 `persona.py`**，那是唯一的"性格文件"。

## 部署到云服务器（7×24 在线）

### 0. 准备

- 一台 Linux 服务器（Ubuntu 22.04 以上，1 核 2G 够用）
- 装好 Docker 和 compose 插件

### 1. 上传代码

在 Mac 上执行：

```bash
rsync -av --exclude '.env' --exclude 'napcat' \
  qq-bot/ 用户名@服务器IP:/opt/dafeiyu/
```

（`.env` 里有你的 key，不进仓库也不走 rsync，稍后在服务器上直接填。）

### 2. 服务器上填 key

```bash
cd /opt/dafeiyu
cp .env.example .env      # 如果 rsync 没带 .env
nano .env                 # 填 OPENAI_API_KEY
```

### 3. 起服务

```bash
docker compose up -d --build
docker compose logs -f bot
```

两个容器：`napcat`（协议端）+ `dafeiyu-bot`（你的机器人）。

### 4. 扫码登录（第一次必须做）

协议端 WebUI 只监听服务器本机。在你 Mac 上开隧道：

```bash
ssh -L 6099:127.0.0.1:6099 用户名@服务器IP
```

保持这个终端不关，浏览器打开 http://127.0.0.1:6099 ，用**小号**扫码登录。

> 用大号的风险：第三方协议端违反腾讯用户协议，可能封号。你自己决定。

### 5. 确认 OneBot WebSocket

在 NapCat WebUI 里看网络配置，确认**正向 WebSocket** 已开启、端口 `3001`。
如果实际端口不同，改 `docker-compose.yml` 里 bot 的 `ONEBOT_WS`，然后 `docker compose up -d`。

### 6. 群里测试

拉它的小号进群，发 `@大肥鱼 在吗`。看日志：

```bash
docker compose logs -f bot
```

看到 `[收到]` 和 `[回复]` 就成了。

### 7. 安全

- `6099` 端口**不要**对公网开放，只走 SSH 隧道（compose 里已经只绑 127.0.0.1）
- 云服务器安全组只放 SSH
- `.env` 永不提交（已在 `.gitignore` 里）

## 调整行为

| 想改 | 改哪里 |
|---|---|
| 性格、口头禅 | `persona.py` |
| 什么时候回（@ / 关键词） | `bot.py` 的 `should_reply()` |
| 回得多快 | `.env` 的 `COOLDOWN` |
| 记得多少上下文 | `bot.py` 的 `MAX_HISTORY` |

## 什么时候才需要 agent

现在这套够用了。等你想加这些，才轮到 agent：

- 让它自己查天气、查梗、搜东西（**加工具**）
- 让它记住"上次谁惹过它"（**加记忆**）
- 让它看群气氛自己决定要不要插话（**加自主判断**，风险最高）

每加一样，反应变慢、变贵、更难预测。一次只加一样，加完观察一周。

## 常见问题

**连不上 NapCat？**
`docker compose logs -f napcat` 看协议端有没有起来；确认没有卡在扫码。

**重启后要重新扫码？**
挂载了 `./napcat/qq` 一般能保留登录态；如果重建了容器目录，可能要重扫。

**它不说话？**
检查三处：群消息里有没有 @ 到它的小号、`BOT_NAMES` 里的词对不对、`COOLDOWN` 是不是还没过。
