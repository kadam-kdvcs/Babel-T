# 临时部署指南（供队友/他人异地审查）

> 目的：把本地 Streamlit 页面**临时**暴露给不在同一局域网的人访问（如队友异地审查）。
> 适用场景：阶段 1~4 任意版本（本质是任意 Streamlit 应用）。
> 上次实测：2026-08-09 阶段 3 完成时（翻译 + LLM 审校真实模式）。

## 总体思路

本地页面默认只允许本机访问（绑定 127.0.0.1）。让别人访问有两级方案：

| 级别 | 方案 | 谁能访问 |
|---|---|---|
| 局域网 | `--server.address 0.0.0.0` | 同一 Wi-Fi/路由器下的人 |
| 公网 | cloudflared 临时隧道（本指南主推） | 任何网络的人（异地） |

## 步骤 1：启动页面（本机）

```bash
cd /d/G/python_coding/proj_vibe_coding/arabic-review-mvp
streamlit run app.py
```

确认本机可访问：浏览器打开 http://localhost:8501

> 注意：改过 `.env`（如 REVIEW_ENGINE）必须**重启进程**才生效（load_dotenv 只在启动时读一次）。

## 步骤 2（可选）：局域网绑定

同一局域网的人访问时，把服务绑定到所有网卡：

```bash
streamlit run app.py --server.address 0.0.0.0 --server.headless true
```

- 查看本机局域网 IP：`ipconfig`（找 IPv4，如 192.168.1.8）
- 队友访问：http://192.168.1.8:8501
- 若队友连不上：Windows 防火墙拦截了 8501 入站，需放行（「允许应用通过防火墙」→ 放行 Python/streamlit，或入站规则放行 TCP 8501）

## 步骤 3：公网临时隧道（cloudflared，推荐）

免费、**无需注册账号**、一条命令生成 https 公网 URL，队友零安装直接打开。

### 3.1 下载工具（放到用户目录，别放项目里污染 git）

```bash
cd /c/Users/<你的用户名>
curl -sL -o cloudflared.exe "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
```

> 备选：`winget install --id Cloudflare.cloudflared`（若装有 winget）。
> 下载过一次后不用再下（本机已存在 `C:\Users\Lenovo\cloudflared.exe`）。

### 3.2 启动隧道（转发本地 8501）

```bash
/c/Users/<你的用户名>/cloudflared.exe tunnel --url http://localhost:8501
```

启动后数秒内日志里会出现一行：

```
Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):
https://xxxxxxxx-xxxx-xxxx-xxxx.trycloudflare.com
```

### 3.3 提取 URL 并验证

- 把 `https://xxx.trycloudflare.com` 复制出来发给对方
- 自己先浏览器打开验证一遍（确保链路通再发）

## 注意事项（重要）

1. **临时性**：该 URL 是 Cloudflare 免费随机分配，**关闭 cloudflared 进程 / 关机 / 长时间空闲**都会失效，需重新执行 3.2 拿新 URL
2. **额度消耗**：拿到 URL 的任何人点「开始翻译与审校」都会调用真实阿里云/DeepSeek API，**消耗你的额度**——提醒对方别反复狂点
3. **安全**：隧道只转发页面流量，密钥仍在本地 `.env`（gitignored），不会经过隧道泄露；但页面本身无鉴权，**审查完务必关闭隧道**
4. **关闭方式**：结束 cloudflared 进程（任务管理器或 `taskkill //F //IM cloudflared.exe`）；streamlit 页面同理可停

## 备选方案（一句话对比）

| 方案 | 优点 | 缺点 |
|---|---|---|
| **Tailscale**（虚拟局域网） | 稳定、私密、可长期 | 双方都要装客户端并登录 |
| **cpolar**（国内内网穿透） | 国内访问速度快 | 需注册账号 |
| **Streamlit Community Cloud** | 官方云部署，URL 固定 | 需 GitHub 授权；代码托管云端；国内访问可能不稳 |

## 本次实测记录（2026-08-09）

- 本机 IP 192.168.1.8；cloudflared 生成 URL `https://perspectives-renewal-guests-thousand.trycloudflare.com`，异地可访问验证通过
- 隧道进程与 streamlit 进程均在本会话后台运行；审查结束后需关闭
