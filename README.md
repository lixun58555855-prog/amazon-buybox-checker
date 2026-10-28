# 亚马逊加拿大站 (Amazon.ca) ASIN 购物车 (Buy Box) 检测工具

这是一个专门针对亚马逊加拿大站点（Amazon.ca）打造的 HTML 可视化检测工具，完美解决国内/异地访问亚马逊时因 IP 归属地导致“不配送当前地址”而隐蔽购物车的问题。

## ✨ 核心检测流程

1. **第一步（自动定位加拿大本土邮编）**：
   - 自动驱动无头或可视 Chromium 浏览器访问 `amazon.ca`。
   - 调用亚马逊底层地址注入接口，将送货地址一键变更为加拿大本土真实邮编（默认多伦多 `M5V 2T6`，亦可自由切换温哥华 `V6B 1A1`、蒙特利尔 `H3B 2Y5` 等）。
   - 彻底解除“无法配送到您所在地区”的误判。

2. **第二步（源代码级别判断购物车是否存在）**：
   - 检索并比对页面 HTML 源代码中真实的节点标记：
     - `#add-to-cart-button` 或 `name="submit.add-to-cart"`
     - `#buy-now-button` 或 `name="submit.buy-now"`
     - `#desktop_buybox` 模块容器
   - 智能识别 5 种状态：
     - 🟢 **购物车正常在售**：页面存在 Add to cart 按钮。
     - 🔴 **购物车被抑制/仅更多选项**：无 Add to cart，提示 "See all buying choices"。
     - ⚪ **缺货/不可售**：提示 "Currently unavailable"。
     - ❌ **页面变狗/不存在**：404 Page Not Found。
     - ⚠️ **无购物车**：其他原因导致购物车丢失。
   - 同步抓取：商品标题、当前售价、赢得购物车的卖家（Winner Seller）、确认配送地等关键数据。

---

## 🚀 启动与使用方式

### 方式一：双击启动 Web 可视化界面（推荐）
在当前目录下双击运行：
```bat
run.bat
```
系统会自动开启后台服务并在默认浏览器打开：
👉 **http://127.0.0.1:8765**

**Web 界面亮点**：
- 支持单条或批量输入 ASIN 或亚马逊商品完整 URL（每行一个）。
- 提供一键切换多伦多、温哥华等城市邮编。
- 可选“静默后台模式”（高速）或“可视浏览器调试模式”（亲眼看浏览器操作）。
- 实时动态步骤进度条与数据看板（总数、正常数、丢失数、缺货数）。
- 结果支持一键导出为 **CSV / Excel** 表格。

---

### 方式二：命令行极速检测 (CLI)
直接在终端或命令行中检测单个或多个 ASIN：
```powershell
# 检测单个 ASIN 或链接
python check_cli.py "https://www.amazon.ca/dp/B0B53CQYW1"

# 批量检测多个 ASIN
python check_cli.py B0B53CQYW1 B0XXXXXXXX B0YYYYYYYY

# 指定其他加拿大邮编（例如温哥华 V6B 1A1）
python check_cli.py B0B53CQYW1 --postal "V6B 1A1"

# 开启可视化浏览器窗口运行
python check_cli.py B0B53CQYW1 --visible
```

---

## 📁 目录文件清单

- `backend.py`：本地 Web 服务与 Playwright 自动化探测核心引擎（无需额外第三方 HTTP 依赖）。
- `index.html`：现代响应式 HTML 界面，包含仪表盘统计、表格展示与导出。
- `check_cli.py`：终端命令行批量检测脚本。
- `run.bat`：Windows 一键双击启动脚本。
