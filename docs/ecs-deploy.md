# Babel-T 部署到阿里云 ECS

本文用于把 Babel-T 作为独立的 Streamlit 服务部署到 Ubuntu ECS。它不修改个人博客的 Nginx 网站目录，也不把 Babel-T 合并到 `Personal_Web` 仓库。

## 访问方式

临时公网测试：

```text
博客：     http://公网IPv4
Babel-T：  http://公网IPv4:8501
```

域名审核完成后，推荐改为：

```text
blog.你的域名       → Nginx → 个人博客静态文件
babel-t.你的域名    → Nginx → 127.0.0.1:8501
```

届时不再把 8501 暴露给公网，只保留 22、80、443。

## 一次性服务器初始化

以下操作需要使用 root SSH 会话执行。命令只创建 Babel-T 自己的用户、目录、systemd 服务和 API 配置，不会改动博客目录。

### 1. 创建运行用户和目录

```bash
adduser --system --group --home /var/lib/babel-t babelt
install -d -m 755 -o webdeploy -g webdeploy /opt/babel-t
install -d -m 755 -o webdeploy -g webdeploy /opt/babel-t/releases
install -d -m 755 -o babelt -g babelt /var/lib/babel-t
install -d -m 750 -o root -g babelt /etc/babel-t
```

`webdeploy` 负责上传代码，`babelt` 只负责运行程序。代码目录和博客目录相互独立。

### 2. 安装 Python 环境

```bash
apt update
apt install -y python3 python3-venv curl
```

### 3. 安装 systemd 服务

把仓库中的 `deploy/babel-t.service` 上传到服务器，然后执行：

```bash
install -m 644 deploy/babel-t.service /etc/systemd/system/babel-t.service
systemctl daemon-reload
systemctl enable babel-t.service
```

首次启动由 GitHub Actions 完成。因为临时阶段要直接通过 `:8501` 访问，服务模板暂时监听 `0.0.0.0:8501`。

### 4. 配置 API 密钥

编辑受限配置文件：

```bash
nano /etc/babel-t/babel-t.env
```

内容格式如下，值替换成你自己的密钥：

```dotenv
TRANSLATION_ENGINE=api
ALIYUN_ACCESS_KEY_ID=你的阿里云AccessKeyID
ALIYUN_ACCESS_KEY_SECRET=你的阿里云AccessKeySecret
REVIEW_ENGINE=api
DEEPSEEK_API_KEY=你的DeepSeekAPIKey
```

保存后设置权限：

```bash
chown root:babelt /etc/babel-t/babel-t.env
chmod 640 /etc/babel-t/babel-t.env
```

API 密钥不放在 GitHub Secrets 中供工作流使用，也不放入仓库；它们只由服务器上的 systemd 服务读取。

### 5. 允许自动部署用户重启服务

```bash
cat >/etc/sudoers.d/babel-t-deploy <<'EOF'
Cmnd_Alias BABEL_T_SERVICE = /usr/bin/systemctl restart babel-t.service, /usr/bin/systemctl is-active --quiet babel-t.service
webdeploy ALL=(root) NOPASSWD: BABEL_T_SERVICE
EOF
chmod 440 /etc/sudoers.d/babel-t-deploy
visudo -cf /etc/sudoers.d/babel-t-deploy
```

这只允许 `webdeploy` 重启和检查 Babel-T 服务，不授予完整 root 权限。

## GitHub Actions Secrets

在 Babel-T 仓库的 Settings → Secrets and variables → Actions 中新增：

| Secret | 值 |
|---|---|
| `SERVER_HOST` | ECS 公网 IPv4 |
| `SERVER_PORT` | `22` |
| `SERVER_USER` | `webdeploy` |
| `SERVER_SSH_KEY` | 与服务器 `authorized_keys` 匹配的无密码短语部署私钥 |
| `SERVER_KNOWN_HOSTS` | ECS SSH 主机指纹 |
| `DEPLOY_PATH` | `/opt/babel-t` |

`SERVER_SSH_KEY` 只能放私钥文本，包括完整的 `BEGIN ... PRIVATE KEY`、正文和 `END ... PRIVATE KEY`，换行必须保留。不能填写 `.pub` 文件。

工作流会自动：

1. 检查 SSH 和部署目录权限；
2. 上传代码到按 commit SHA 命名的 release 目录；
3. 创建或更新该 release 的 Python 虚拟环境；
4. 切换 `current` 符号链接；
5. 重启 `babel-t.service`；
6. 检查 Streamlit 健康接口。

## 安全组

临时测试至少需要：

| 端口 | 协议 | 用途 |
|---|---|---|
| 22 | TCP | SSH 和 GitHub Actions 部署 |
| 80 | TCP | 个人博客 HTTP |
| 8501 | TCP | 临时直接访问 Babel-T |

域名和 HTTPS 配置完成后，删除 8501 的公网入站规则，并让 Nginx 反向代理到 `127.0.0.1:8501`，再开放 443。

## 常用排查

```bash
systemctl status babel-t --no-pager
journalctl -u babel-t -n 100 --no-pager
ss -tulpn | grep ':8501'
curl -I http://127.0.0.1:8501/_stcore/health
```

如果本机健康检查成功、外部仍无法访问，优先检查阿里云安全组 TCP 8501；如果端口可达但页面加载异常，检查 systemd 日志和 Streamlit WebSocket 请求。

## 未来切换到域名

1. 将 `babel-t.service` 的 `--server.address 0.0.0.0` 改为 `--server.address 127.0.0.1`；
2. 在 Nginx 增加 `babel-t.你的域名` 的反向代理配置；
3. 配置 HTTPS 和 WebSocket 转发；
4. 验证 `blog.你的域名` 与 `babel-t.你的域名` 分别打开正确项目；
5. 从安全组移除 8501。
