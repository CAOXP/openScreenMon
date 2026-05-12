<div align="center">
  <h1>👁️ ScreenMon</h1>
  <p><b>ScreenMon智能屏幕与个人上下文累计工具</b></p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.11+-blue.svg" alt="Python Version" />
    <img src="https://img.shields.io/badge/Platform-Windows-lightgrey.svg" alt="Windows Only" />
    <img src="https://img.shields.io/badge/LLM-OpenAI%20%7C%20DashScope-orange.svg" alt="LLM Support" />
    <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License" />
  </p>
</div>

## 📖 项目简介

**ScreenMon** 是一款专为个人设计的“电脑截图日志”工具。它的核心目标是**无感、持续地记录屏幕活动，为个人及 AI 协作积累详尽的上下文素材**。

本工具专注于**数据的忠实记录与累计**。在 AI 协作场景下，它负责提供“原始记录”和“工作证据”，让 AI 能够通过回溯这些历史快照和日志，了解你的工作轨迹。它不替代你进行思考或提炼，而是充当你的“数字化底片库”，确保在需要时，每一刻的工作细节都有据可查。

### 核心价值
- **个人上下文累计 (Context Accumulation)**：通过高频屏幕快照，为个人知识库和 AI 助手提供完整的工作背景素材。
- **原始素材留存**：忠实记录每一刻的屏幕状态，解决“记不清过去几小时做了什么”的问题。
- **辅助 AI 对齐**：将记录作为原始上下文提供给 AI，使其能基于真实发生的记录进行辅助，而非凭空猜测。
- **隐私与本地化**：所有截图和数据库默认保存在本地，由用户完全控制数据的流向。

---

## ✨ 核心特性

- 🤖 **多模态 AI 记录增强**
  - 利用 OpenAI (GPT-4o) 或通义千问等模型，对屏幕画面进行基础描述并记录，方便后续检索。
- 💤 **智能空闲检测**
  - 自动识别屏幕静止状态，停止无效记录，节省存储空间与 Token。
- 📅 **结构化日志存储**
  - 采用 SQLite 数据库记录“时间-快照路径-基础描述”，确立清晰的时间轴。
- 📷 **多源采集**
  - 支持多显示器截图及摄像头拍照（可选），全方位留存工作现场。
- 📧 **每日记录汇总**
  - 每天定时将当日累计的记录汇总为 Markdown 日报发送至邮箱，作为个人复盘的原始素材。

---

## 🛠️ 路线图 (Roadmap) - 累计效率优化

为了更好地进行上下文累计并降低成本，我们计划引入：

- 🔍 **本地 OCR 预处理**
  - **文本化累计**：在存储前通过本地 OCR 提取文字，实现图片与文本的双重累计。
  - **Token 节省**：将提取出的文本作为上下文发送，大幅降低对多模态 API 的依赖。
  - **全文检索**：支持对历史累计数据进行关键词搜索。
- 🧠 **本地向量化存储**
  - 将累计的上下文转化为向量索引，方便在与 AI 对话时精准召回相关的历史记录。

---

## 🚀 快速开始

### 安装与运行

1. **环境准备**：Python 3.11+ / Windows 10/11。
2. **安装依赖**：`pip install -r requirements.txt`。
3. **配置文件**：复制 `config.example.yaml` 为 `config.yaml` 并填写相关 API Key。
4. **启动工具**：`python -m screenmon --config config.yaml --gui`。

---

## 📁 数据存储

- `data/monitor.db`: 记录索引数据库。
- `data/screenshots/`: 原始快照文件。
- `logs/summary_*.md`: 每日生成的原始记录汇总。

---

## 📄 License

本项目基于 [MIT License](LICENSE) 开源。
