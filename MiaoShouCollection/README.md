# MiaoShouCollection - 妙手采集浏览器控制模块

这是一个独立的、可保存配置的浏览器控制模块，用于"妙手采集"功能。

## 功能特性

- 独立的浏览器配置和profile管理
- 支持Chrome远程调试（默认端口9224）
- 指纹伪装功能
- 代理配置支持
- 命令行界面

## 安装

```bash
pip install -r requirements.txt
playwright install chromium
```

## 配置

1. 复制配置模板：
```bash
cp .env.example .env
```

2. 编辑 `.env` 文件，配置浏览器参数。

主要配置项：
- `CHROME_PROFILE_DIR`: Chrome配置目录
- `CHROME_REMOTE_DEBUGGING_PORT`: 远程调试端口（默认9224）
- `CHROME_EXTENSION_DIR`: 扩展目录
- `CHROME_FINGERPRINT_MASK_ENABLED`: 启用指纹伪装

## 使用方法

### 启动Chrome浏览器

```bash
python -m miao_shou_collection.cli launch --url https://www.ozon.ru/
```

### 连接到现有浏览器

```bash
python -m miao_shou_collection.cli connect --cdp-url http://127.0.0.1:9224
```

### 显示当前配置

```bash
python -m miao_shou_collection.cli config
```

### 作为Python模块使用

```python
from miao_shou_collection.browser import BrowserClient

# 使用上下文管理器
with BrowserClient() as client:
    page = client.get_page("https://www.ozon.ru/")
    print(page.title())
```

## 目录结构

```
MiaoShouCollection/
├── .env.example          # 配置模板
├── .gitignore           # Git忽略文件
├── README.md           # 说明文档
├── requirements.txt    # Python依赖
├── miao_shou_collection/  # Python包
│   ├── __init__.py
│   ├── browser.py      # 浏览器控制模块
│   ├── cli.py          # 命令行接口
│   └── config.py       # 配置管理
└── profiles/           # 浏览器配置目录
```

## 配置说明

### 浏览器配置

- `CHROME_PROFILE_DIR`: Chrome用户数据目录，用于保存登录状态、cookies等
- `CHROME_EXTENSION_DIR`: Chrome扩展目录
- `CHROME_REMOTE_DEBUGGING_PORT`: 远程调试端口，用于CDP连接
- `CHROME_CDP_URL`: 如果已有浏览器实例，可直接连接

### 指纹伪装

启用指纹伪装可以避免被网站检测为自动化浏览器：

```env
CHROME_FINGERPRINT_MASK_ENABLED=true
CHROME_FINGERPRINT_USER_AGENT=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
CHROME_FINGERPRINT_LOCALE=zh-CN
```

### 代理配置

```env
CHROME_PROXY_SERVER=http://127.0.0.1:7890
```

## 注意事项

1. 首次使用需要安装Playwright浏览器：`playwright install chromium`
2. 浏览器配置目录会保存登录状态，请妥善保管
3. 指纹伪装功能可能需要根据目标网站调整参数
4. 远程调试端口不要与其他服务冲突