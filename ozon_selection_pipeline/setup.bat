@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo  ============================================================
echo    Ozon Selection Pipeline - 部署脚本
echo  ============================================================
echo.

:: ── 1. 检查 Python ──
echo [1/6] 检查 Python...
set PYTHON_CMD=
for %%p in (py -3.9 python python3 py) do (
    %%p --version >nul 2>&1
    if !errorlevel! equ 0 (
        set PYTHON_CMD=%%p
        goto :python_found
    )
)
echo [错误] 未找到 Python。请安装 Python 3.9+
pause
exit /b 1

:python_found
%PYTHON_CMD% --version
echo  Python 路径: %PYTHON_CMD%
echo.

:: ── 2. 安装 Python 依赖 ──
echo [2/6] 安装 Python 依赖...
if exist "requirements.txt" (
    %PYTHON_CMD% -m pip install -r requirements.txt --quiet
    if !errorlevel! neq 0 (
        echo [警告] pip install 失败，请手动执行: pip install -r requirements.txt
    ) else (
        echo  依赖安装完成
    )
) else (
    echo [警告] requirements.txt 不存在，跳过
)
echo.

:: ── 3. 安装 Playwright 浏览器 ──
echo [3/6] 安装 Playwright 浏览器 (Chromium)...
%PYTHON_CMD% -m playwright install chromium
if !errorlevel! neq 0 (
    echo [警告] Playwright 浏览器安装失败，请手动执行: playwright install chromium
) else (
    echo  Playwright Chromium 安装完成
)
echo.

:: ── 4. 配置 .env ──
echo [4/6] 配置环境变量...
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo  已从 .env.example 创建 .env
        echo.
        echo  *** 请用文本编辑器打开 .env 并填写以下必填项: ***
        echo    - DB_HOST / DB_USER / DB_PASSWORD (数据库连接)
        echo    - MAOZI_TOKEN (毛子ERP令牌)
        echo    - CHROME_EXTENSION_ID (插件安装后可获取)
        echo.
    ) else (
        echo [警告] .env.example 不存在，请手动创建 .env
    )
) else (
    echo  .env 已存在，跳过
)
echo.

:: ── 5. 测试数据库连接 ──
echo [5/6] 测试数据库连接...
%PYTHON_CMD% -c "import pymysql; from ozon_pipeline.config import settings; c=pymysql.connect(host=settings.db_host,port=settings.db_port,user=settings.db_user,password=settings.db_password,database=settings.db_name,connect_timeout=5); print('  数据库连接成功:', settings.db_name); c.close()" 2>nul
if !errorlevel! neq 0 (
    echo [警告] 数据库连接失败，请检查 .env 中的数据库配置
    echo  确保 MySQL 已运行并已创建数据库 ozon_selection
) else (
    echo  数据库连接成功，尝试执行迁移...
    for %%f in (sql\00*.sql) do (
        echo   执行 %%f ...
        %PYTHON_CMD% -c "import pymysql; from ozon_pipeline.config import settings; c=pymysql.connect(host=settings.db_host,port=settings.db_port,user=settings.db_user,password=settings.db_password,database=settings.db_name,connect_timeout=5); sql=open('%%f','r',encoding='utf-8').read(); [c.cursor().execute(s) for s in sql.split(';') if s.strip() and not s.strip().startswith('--')]; c.commit(); c.close(); print('   OK')" 2>nul
    )
)
echo.

:: ── 6. 完成 ──
echo [6/6] ============================================================
echo.
echo   部署完成！
echo.
echo   后续手动步骤:
echo   [1] 编辑 .env 填写 MAOZI_TOKEN
echo   [2] 打开 Chrome, 进入 chrome://extensions/
echo   [3] 开启"开发者模式"，点击"加载已解压的扩展程序"
echo   [4] 选择 ..\maozi-plugin-2.3.2 目录
echo   [5] 复制扩展ID，填入 .env 的 CHROME_EXTENSION_ID
echo   [6] 用 run_gui.bat 启动采集
echo.
echo   命令行手动启动 Chrome (可选):
echo   "%ProgramFiles%\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
echo   (如果 x64: "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe")
echo.
echo  ============================================================
pause
