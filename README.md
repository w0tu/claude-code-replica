# 🚀 Claude Code Replica — Autonomous Terminal Coding Assistant

<div align="center">

![License](https://img.shields.io/badge/license-MIT-blue.svg?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg?style=for-the-badge&logo=python&logoColor=white)
![Terminal](https://img.shields.io/badge/Terminal-Rich%20%7C%20Prompt--Toolkit-black.svg?style=for-the-badge)
![Status](https://img.shields.io/badge/Release-v1.0.0-success.svg?style=for-the-badge)

**A powerful, developer-centric agentic pair programmer in your Linux terminal. Built with rate-limit resiliency, expandable thinking blocks, and real-time codebase autocomplete.**

</div>

---

## ⚡ Features

- 🛡️ **Rate Limit Protection**: Built-in backoff and pacing engine to navigate API limits smoothly.
- 🎨 **Rich Terminal Visuals**: Dynamic animations, interactive command menus (`/`), and `@` file references.
- 💡 **Super Thinking View**: Expandable chain-of-thought blocks with detailed reasoning inspection.
- 📊 **Usage Heatmap**: Interactive GitHub-style 32-square heatmap of your coding sessions via `/usage`.
- 💻 **Syntax Highlighting**: Real-time code formatting with Monokai styling in your shell.

---

## 🚀 Usage

```bash
python -m claude_replica.cli
```

### Slash Commands
- `/usage` — Show monthly usage activity heatmap
- `/model` — Switch inference model
- `@filename` — Autocomplete files from active workspace
- `/help` — List commands and shortcuts

---

## 📜 License
MIT License. Created by [w0tu](https://github.com/w0tu).
