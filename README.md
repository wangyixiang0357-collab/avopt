# 农光互补布局优化系统

Python 农光互补布局评估与五水平寻优工具，支持冬小麦、冬油菜、夏玉米、大豆、花生；发电计算计入排间遮挡、横向间隙、散射遮挡和电气失配估算。

默认全部采用 ERA5 历史再分析（由 Open-Meteo 提供，无需数据账号），年份 2020—2026。未完成年份仅按实际已获取的日期计算时段结果。当前大荔参考点已含 2020-01-01 至 2026-10-30 的公开气象缓存。

## 这份代码如何使用

GitHub 用于保存代码。GitHub Pages 只运行静态页面，不能执行本程序的 Python 计算。需要异地打开并计算时，把仓库连接到支持 Python/Docker 的 Web 服务。

### 本机运行

```text
python -m pip install -r requirements.txt
python start_avopt.py
```

本机地址 `http://127.0.0.1:8765/`。这个地址只在运行程序的电脑上有效。

### 上传到 GitHub

1. 登录 GitHub，新建仓库，例如 `avopt`。项目代码尚未计划公开时，选择 **Private**。
2. 把上传包解压，将解压后目录内的文件和文件夹上传到仓库根目录，使 `Dockerfile`、`render.yaml`、`AVOpt/`、`AVSim/` 位于根目录。
3. 提交保存。上传 ZIP 文件本身只会存档；云服务需要能够读取解压后的源码。

上传包采用文件清单整理，包含运行源码和公开气象缓存。项目原始试验文档、实测报告、本机运行依赖、日志、浏览器方案和用户凭据保留在原电脑。

### 部署到 Render

1. 登录 Render，连接上述 GitHub 仓库。
2. 创建 Blueprint，使用仓库根目录的 `render.yaml`；或新建 Web Service，运行环境选 **Docker**。
3. 核对服务套餐和费用后创建。配置文件默认指定 Free；免费实例有休眠、内存、计算与用量限制，大范围寻优或多人使用时可由您自行选择合适配置。
4. Blueprint 会生成 `AVOPT_ACCESS_PASSWORD`，用户名默认为 `avopt`。在云服务的 Environment 页面查看密码，分享访问链接时把密码单独告知使用者。手动创建 Web Service 时需自行设置至少 12 位密码。
5. 部署完成后，使用云平台给出的 `https://…onrender.com` 地址在异地访问。浏览器会先要求用户名和密码。运行发生在云端，原电脑可以关机。

`/api/health` 为不含计算内容的公开健康检查，其余页面和接口均需访问密码。密码只放云平台环境变量，不写入 GitHub。云服务提供 HTTPS；不建议使用明文 HTTP 传递访问密码。

Docker 配置安装中文字体，并由 Gunicorn 提供外部 HTTP 服务。内部调用原有计算路由，不另写一套计算模型。云端监听由平台的 `PORT` 决定；本机原启动入口仍只监听本机地址。

缓存写入实例本地文件系统。未配置持久化磁盘时，新获取缓存可能在重启/重新部署后重新下载；上传包附带的基础缓存会随镜像恢复。页面保存的方案在访问者浏览器内，可导出 JSON/XLSX。

## 模型与数据口径

结果用于方案估算，作物和光学参数需要当地实测校准。完整生育季沿用既有目标产量校准；截断季只计算已有日期内的生物量折算当量，未补出完整收获产量。时段电量不年化，年度固定成本按实际日期比例计提。

ERA5 原始响应与请求信息位于 `data/open_meteo/`。太阳辐射单位为 MJ/m²/日；10 m 风速按 FAO-56 换算为模型需要的 2 m 风速。ERA5-Land 账号下载/本地导入保留为旧模式。

## 检查

```text
python -m unittest discover -s tests -p test_cloud_wsgi.py -v
```

本机已验证云端入口的密码保护、路由、错误状态和请求长度限制。完整 Docker 镜像和真实云部署需在具备 Docker/云账号的环境完成验证。此包尚未自动上传 GitHub或部署云服务。

## 参考

- [GitHub Pages 官方说明](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)
- [Render Web Services](https://render.com/docs/web-services)
- [Render Docker 部署](https://render.com/docs/docker)
- [Render 免费实例限制](https://render.com/docs/free)
- [Open-Meteo 历史气象与源数据引用](https://open-meteo.com/en/docs/historical-weather-api)
- [FAO-56 气象资料](https://www.fao.org/4/x0490e/x0490e07.htm)
