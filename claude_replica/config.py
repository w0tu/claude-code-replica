"""
Configuration management for Coder / Claude Code Replica.
Handles API keys, default providers, Claude model mapping, rate limit protection & effort tiers.
"""

import os
import sys
import json
import time
import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

CONFIG_DIR = Path.home() / ".claude-replica"
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_DIR = CONFIG_DIR / "sessions"

# User Documents Directory for Code Sessions & Apps
def get_documents_dir() -> Path:
    """Find or create Documents folder cross-platform under 'my apps'."""
    home = Path.home()
    candidates = [home / "Documents", home / "documents", home]
    for c in candidates:
        if c.exists() and c.is_dir():
            target = c / "my apps"
            target.mkdir(parents=True, exist_ok=True)
            return target
    target = home / "Documents" / "my apps"
    target.mkdir(parents=True, exist_ok=True)
    return target


# Real Clawd ASCII Art from Anthropic's Claude Code (WelcomeV2.tsx)
REAL_CLAWD_ASCII = """   █████████  
  ██▄█████▄██ 
   █████████  
   █ █   █ █  """

# Effort tiers for rate limit protection requested by user:
# 5 prompts for extra high, 10 for high, 20 for medium, 40 for normal, 80 for low
EFFORT_TIERS: Dict[str, Dict[str, Any]] = {
    "extra high": {
        "limit": 5,
        "temperature": 0.05,
        "max_tokens": 8192,
        "description": "Deepest reasoning depth & maximum code quality (5 prompts/hour)"
    },
    "high": {
        "limit": 10,
        "temperature": 0.1,
        "max_tokens": 6144,
        "description": "Thorough analysis & architecture review (10 prompts/hour)"
    },
    "medium": {
        "limit": 20,
        "temperature": 0.15,
        "max_tokens": 4096,
        "description": "Balanced coding & problem solving (20 prompts/hour)"
    },
    "normal": {
        "limit": 40,
        "temperature": 0.2,
        "max_tokens": 4096,
        "description": "Standard daily development (40 prompts/hour)"
    },
    "low": {
        "limit": 80,
        "temperature": 0.3,
        "max_tokens": 2048,
        "description": "Fast lightweight queries & rapid edits (80 prompts/hour)"
    }
}


# Human-friendly Claude model names mapped to optimal backend IDs
CLAUDE_MODEL_MAPPINGS = {
    "groq": {
        "claude-3-7-sonnet": {
            "display_name": "Claude 3.7 Sonnet",
            "backend_id": "qwen/qwen3.8-27b",
            "description": "Smartest model for reasoning, deep coding, and agentic workflows"
        },
        "claude-3-5-sonnet": {
            "display_name": "Claude 3.5 Sonnet",
            "backend_id": "openai/gpt-oss-120b",
            "description": "High performance coding & reasoning workhorse"
        },
        "claude-3-5-haiku": {
            "display_name": "Claude 3.5 Haiku",
            "backend_id": "openai/gpt-oss-20b",
            "description": "Ultra-fast lightweight model for quick edits and searches"
        },
        "claude-3-opus": {
            "display_name": "Claude 3 Opus",
            "backend_id": "openai/gpt-oss-120b",
            "description": "Demanding structural analysis and complex logic"
        },
        "fable-5": {
            "display_name": "Fable 5",
            "backend_id": "qwen/qwen3.8-27b",
            "description": "Full-stack website generation, UI/UX architecture & frontend engineering"
        },
        "fable-5-1": {
            "display_name": "Fable 5.1",
            "backend_id": "openai/gpt-oss-120b",
            "description": "Autonomous web architect & advanced component engineering"
        },
        "openai/gpt-oss-120b": {
            "display_name": "GPT-OSS 120B",
            "backend_id": "openai/gpt-oss-120b",
            "description": "OpenAI 120B Open Weights Reasoning & Coding Engine"
        },
        "gpt-oss-120b": {
            "display_name": "GPT-OSS 120B",
            "backend_id": "openai/gpt-oss-120b",
            "description": "OpenAI 120B Open Weights Reasoning & Coding Engine"
        }
    },
    "gemini": {
        "gemini-3.5-flash-lite": {
            "display_name": "Gemini 3.5 Flash-Lite",
            "backend_id": "gemini-3.5-flash-lite",
            "description": "Cheapest model with highest rate limits and instant latency"
        },
        "gemini-3.6-flash": {
            "display_name": "Gemini 3.6 Flash",
            "backend_id": "gemini-3.6-flash",
            "description": "Next-gen multimodal reasoning & coding engine"
        },
        "claude-3-7-sonnet": {
            "display_name": "Claude 3.7 Sonnet (Gemini 3.6 Flash)",
            "backend_id": "gemini-3.6-flash",
            "description": "Smartest model for reasoning, deep coding, and agentic workflows"
        },
        "claude-3-5-sonnet": {
            "display_name": "Claude 3.5 Sonnet (Gemini 3.6 Flash)",
            "backend_id": "gemini-3.6-flash",
            "description": "High performance coding & reasoning workhorse"
        },
        "claude-3-5-haiku": {
            "display_name": "Claude 3.5 Haiku (Gemini 3.5 Flash-Lite)",
            "backend_id": "gemini-3.5-flash-lite",
            "description": "Ultra-fast lightweight model with highest rate limits"
        },
        "claude-3-opus": {
            "display_name": "Claude 3 Opus (Gemini 3.6 Flash)",
            "backend_id": "gemini-3.6-flash",
            "description": "Demanding structural analysis and complex logic"
        },
        "fable-5": {
            "display_name": "Fable 5",
            "backend_id": "gemini-3.5-flash-lite",
            "description": "Full-stack website generation & UI engineering"
        },
        "fable-5-1": {
            "display_name": "Fable 5.1",
            "backend_id": "gemini-3.6-flash",
            "description": "Autonomous web architect & advanced component engineering"
        }
    },
    "google": {
        "gemini-3.5-flash-lite": {
            "display_name": "Gemini 3.5 Flash-Lite",
            "backend_id": "gemini-3.5-flash-lite",
            "description": "Cheapest model with highest rate limits and instant latency"
        },
        "gemini-3.6-flash": {
            "display_name": "Gemini 3.6 Flash",
            "backend_id": "gemini-3.6-flash",
            "description": "Next-gen multimodal reasoning & coding engine"
        },
        "claude-3-7-sonnet": {
            "display_name": "Claude 3.7 Sonnet (Gemini 3.6 Flash)",
            "backend_id": "gemini-3.6-flash",
            "description": "Smartest model for reasoning, deep coding, and agentic workflows"
        },
        "claude-3-5-sonnet": {
            "display_name": "Claude 3.5 Sonnet (Gemini 3.6 Flash)",
            "backend_id": "gemini-3.6-flash",
            "description": "High performance coding & reasoning workhorse"
        },
        "claude-3-5-haiku": {
            "display_name": "Claude 3.5 Haiku (Gemini 3.5 Flash-Lite)",
            "backend_id": "gemini-3.5-flash-lite",
            "description": "Ultra-fast lightweight model with highest rate limits"
        },
        "claude-3-opus": {
            "display_name": "Claude 3 Opus (Gemini 3.6 Flash)",
            "backend_id": "gemini-3.6-flash",
            "description": "Demanding structural analysis and complex logic"
        }
    },
    "antigravity": {
        # Gemini 3.8 Flash series
        "gemini-3.8-flash": {
            "display_name": "Gemini 3.8 Flash (Antigravity)",
            "backend_id": "gemini-3.8-flash-low",
            "description": "Google Gemini 3.8 Flash Ultra-Fast Engine (~1.2s latency)"
        },
        "gemini-3.8-flash-low": {
            "display_name": "Gemini 3.8 Flash Fast (Antigravity)",
            "backend_id": "gemini-3.8-flash-low",
            "description": "Ultra-fast Google Gemini 3.8 Flash with minimal reasoning latency"
        },
        "gemini-3.8-flash-medium": {
            "display_name": "Gemini 3.8 Flash Medium (Antigravity)",
            "backend_id": "gemini-3.8-flash-medium",
            "description": "Balanced Google Gemini 3.8 Flash reasoning for general engineering"
        },
        "gemini-3.8-flash-high": {
            "display_name": "Gemini 3.8 Flash High (Antigravity)",
            "backend_id": "gemini-3.8-flash-high",
            "description": "Deep chain-of-thought reasoning for complex algorithms"
        },
        # Gemini 3.7 Flash series
        "gemini-3.7-flash": {
            "display_name": "Gemini 3.7 Flash (Antigravity)",
            "backend_id": "gemini-3.7-flash-medium",
            "description": "Next-gen Google Gemini 3.7 Flash reasoning workhorse"
        },
        "gemini-3.7-flash-low": {
            "display_name": "Gemini 3.7 Flash Fast (Antigravity)",
            "backend_id": "gemini-3.7-flash-low",
            "description": "Fast lightweight Gemini 3.7 Flash with high throughput"
        },
        "gemini-3.7-flash-medium": {
            "display_name": "Gemini 3.7 Flash Medium (Antigravity)",
            "backend_id": "gemini-3.7-flash-medium",
            "description": "Balanced Google Gemini 3.7 Flash for full-stack tasks"
        },
        "gemini-3.7-flash-high": {
            "display_name": "Gemini 3.7 Flash High (Antigravity)",
            "backend_id": "gemini-3.7-flash-high",
            "description": "Deep reasoning Gemini 3.7 Flash for architectural refactoring"
        },
        # Gemini 3.6 Flash series
        "gemini-3.6-flash": {
            "display_name": "Gemini 3.6 Flash (Antigravity)",
            "backend_id": "gemini-3.6-flash-low",
            "description": "Rock-solid Google Gemini 3.6 Flash coding engine"
        },
        "gemini-3.6-flash-low": {
            "display_name": "Gemini 3.6 Flash Fast (Antigravity)",
            "backend_id": "gemini-3.6-flash-low",
            "description": "High-speed Google Gemini 3.6 Flash for quick edits"
        },
        "gemini-3.6-flash-medium": {
            "display_name": "Gemini 3.6 Flash Medium (Antigravity)",
            "backend_id": "gemini-3.6-flash-medium",
            "description": "Standard balanced Gemini 3.6 Flash reasoning"
        },
        "gemini-3.6-flash-high": {
            "display_name": "Gemini 3.6 Flash High (Antigravity)",
            "backend_id": "gemini-3.6-flash-high",
            "description": "Comprehensive Gemini 3.6 Flash deep analysis"
        },
        # Gemini 3.1 Pro series
        "gemini-3.1-pro": {
            "display_name": "Gemini 3.1 Pro (Antigravity)",
            "backend_id": "gemini-3.1-pro-high",
            "description": "Google Gemini 3.1 Pro Flagship Large Model for deep reasoning"
        },
        "gemini-3.1-pro-high": {
            "display_name": "Gemini 3.1 Pro High (Antigravity)",
            "backend_id": "gemini-3.1-pro-high",
            "description": "Google Gemini 3.1 Pro with maximal chain-of-thought reasoning"
        },
        "gemini-3.1-pro-low": {
            "display_name": "Gemini 3.1 Pro Fast (Antigravity)",
            "backend_id": "gemini-3.1-pro-low",
            "description": "Google Gemini 3.1 Pro with streamlined reasoning latency"
        },
        # Anthropic Claude models via Antigravity
        "claude-sonnet-4-6": {
            "display_name": "Claude Sonnet 4.6 (Antigravity)",
            "backend_id": "claude-sonnet-4-6",
            "description": "Anthropic Claude Sonnet 4.6 Thinking via Antigravity"
        },
        "claude-opus-4-6-thinking": {
            "display_name": "Claude Opus 4.6 Thinking (Antigravity)",
            "backend_id": "claude-opus-4-6-thinking",
            "description": "Anthropic Claude Opus 4.6 Deep Thinking via Antigravity"
        },
        "claude-opus-4-6": {
            "display_name": "Claude Opus 4.6 (Antigravity)",
            "backend_id": "claude-opus-4-6-thinking",
            "description": "Anthropic Claude Opus 4.6 Deep Thinking via Antigravity"
        },
        "claude-3-7-sonnet": {
            "display_name": "Claude 3.7 Sonnet (Claude Sonnet 4.6)",
            "backend_id": "claude-sonnet-4-6",
            "description": "Anthropic Claude Sonnet 4.6 Thinking via Antigravity"
        },
        "claude-3-5-sonnet": {
            "display_name": "Claude 3.5 Sonnet (Claude Sonnet 4.6)",
            "backend_id": "claude-sonnet-4-6",
            "description": "Anthropic Claude Sonnet 4.6 Thinking via Antigravity"
        },
        "claude-3-opus": {
            "display_name": "Claude 3 Opus (Claude Opus 4.6)",
            "backend_id": "claude-opus-4-6-thinking",
            "description": "Anthropic Claude Opus 4.6 Deep Thinking via Antigravity"
        },
        "claude-3-5-haiku": {
            "display_name": "Claude 3.5 Haiku (Gemini 3.8 Flash Fast)",
            "backend_id": "gemini-3.8-flash-low",
            "description": "Ultra-fast lightweight coding model via Antigravity"
        },
        # OpenAI Open Weights via Antigravity
        "gpt-oss-120b-medium": {
            "display_name": "GPT-OSS 120B (Antigravity)",
            "backend_id": "gpt-oss-120b-medium",
            "description": "OpenAI 120B Open Weights Reasoning Engine via Antigravity"
        },
        "gpt-oss-120b": {
            "display_name": "GPT-OSS 120B (Antigravity)",
            "backend_id": "gpt-oss-120b-medium",
            "description": "OpenAI 120B Open Weights Reasoning Engine via Antigravity"
        },
        # Fable Autonomous Web Engineering models
        "fable-5": {
            "display_name": "Fable 5 (Gemini 3.8 Flash)",
            "backend_id": "gemini-3.8-flash-low",
            "description": "Autonomous full-stack website generation & UI engineering"
        },
        "fable-5-1": {
            "display_name": "Fable 5.1 (Gemini 3.1 Pro)",
            "backend_id": "gemini-3.1-pro-high",
            "description": "Advanced autonomous web architect & component engineering"
        }
    },
    "anthropic": {
        "claude-3-7-sonnet": {
            "display_name": "Claude 3.7 Sonnet",
            "backend_id": "claude-3-7-sonnet-20250219",
            "description": "Anthropic flagship hybrid model"
        },
        "claude-3-5-sonnet": {
            "display_name": "Claude 3.5 Sonnet",
            "backend_id": "claude-3-5-sonnet-20241022",
            "description": "Anthropic premier coding model"
        },
        "claude-3-5-haiku": {
            "display_name": "Claude 3.5 Haiku",
            "backend_id": "claude-3-5-haiku-20241022",
            "description": "Fast, agile Claude model"
        },
        "claude-3-opus": {
            "display_name": "Claude 3 Opus",
            "backend_id": "claude-3-opus-20240229",
            "description": "Original high-intelligence flagship"
        },
        "fable-5": {
            "display_name": "Fable 5",
            "backend_id": "claude-3-7-sonnet-20250219",
            "description": "Full-stack website generation & UI engineering"
        },
        "fable-5-1": {
            "display_name": "Fable 5.1",
            "backend_id": "claude-3-7-sonnet-20250219",
            "description": "Autonomous web architect & advanced component engineering"
        }
    },
    "omniroute": {
        "auto": {
            "display_name": "Auto Combo (OmniRoute)",
            "backend_id": "groq/qwen/qwen3.8-27b",
            "description": "Smart balanced auto-routing with automatic quota-aware failover across 350+ providers"
        },
        "auto/fast": {
            "display_name": "Auto Fast (OmniRoute)",
            "backend_id": "groq/openai/gpt-oss-20b",
            "description": "Lowest latency routing with instant sub-second response times (0.3s - 1.0s)"
        },
        "auto/coding": {
            "display_name": "Auto Coding (OmniRoute)",
            "backend_id": "auto/coding",
            "description": "Quality-first code generation and architecture routing"
        },
        "groq": {
            "display_name": "Groq Fast (OmniRoute)",
            "backend_id": "groq/openai/gpt-oss-20b",
            "description": "Ultra-fast direct Groq inference via local OmniRoute gateway"
        },
        "gemini": {
            "display_name": "Gemini Flash (OmniRoute)",
            "backend_id": "gemini/gemini-3.5-flash-lite",
            "description": "Instant Google Gemini Flash via local OmniRoute gateway"
        },
        "openrouter": {
            "display_name": "OpenRouter (OmniRoute)",
            "backend_id": "openrouter/auto",
            "description": "OpenRouter multi-model pool via local OmniRoute gateway"
        },
        "claude-3-7-sonnet": {
            "display_name": "Claude 3.7 Sonnet (OmniRoute)",
            "backend_id": "auto/coding",
            "description": "Flagship coding and agentic workflow via OmniRoute Auto-Combo"
        },
        "claude-3-5-sonnet": {
            "display_name": "Claude 3.5 Sonnet (OmniRoute)",
            "backend_id": "auto/coding",
            "description": "High performance coding & reasoning workhorse via OmniRoute"
        },
        "claude-3-5-haiku": {
            "display_name": "Claude 3.5 Haiku (OmniRoute)",
            "backend_id": "auto/fast",
            "description": "Ultra-fast sub-second lightweight model via OmniRoute"
        },
        "claude-3-opus": {
            "display_name": "Claude 3 Opus (OmniRoute)",
            "backend_id": "auto/coding",
            "description": "Demanding structural analysis and complex logic via OmniRoute"
        },
        "antigravity": {
            "display_name": "Antigravity Fast (OmniRoute)",
            "backend_id": "groq/openai/gpt-oss-20b",
            "description": "Google Antigravity instant execution via OmniRoute local gateway"
        },
        "gemini-3.8": {
            "display_name": "Gemini 3.8 Flash (OmniRoute)",
            "backend_id": "groq/openai/gpt-oss-20b",
            "description": "Google Gemini 3.8 Flash Fast via OmniRoute local gateway"
        },
        "gemini-3.8-flash-low": {
            "display_name": "Gemini 3.8 Flash Fast (OmniRoute)",
            "backend_id": "groq/openai/gpt-oss-20b",
            "description": "Google Gemini 3.8 Flash Fast via OmniRoute local gateway"
        },
        "claude-sonnet-4-6": {
            "display_name": "Claude Sonnet 4.6 (OmniRoute)",
            "backend_id": "auto/coding",
            "description": "Claude Sonnet 4.6 reasoning engine via OmniRoute"
        },
        "claude-opus-4-6-thinking": {
            "display_name": "Claude Opus 4.6 Thinking (OmniRoute)",
            "backend_id": "auto/coding",
            "description": "Claude Opus 4.6 thinking engine via OmniRoute"
        }
    }
}


DEFAULT_PROVIDERS = {
    "omniroute": {
        "api_base": "http://localhost:20128/v1",
        "env_key": "OMNIROUTE_API_KEY",
        "default_model": "auto/fast",
        "available_models": [
            "auto/fast",
            "auto",
            "auto/coding",
            "groq",
            "gemini",
            "openrouter",
            "antigravity",
            "gemini-3.8",
            "claude-sonnet-4-6",
            "claude-opus-4-6-thinking",
            "claude-3-7-sonnet",
            "claude-3-5-sonnet",
            "claude-3-5-haiku",
            "claude-3-opus"
        ],
        "backend_fallback_pool": [
            "auto/fast",
            "auto",
            "groq/openai/gpt-oss-20b",
            "gemini/gemini-3.6-flash"
        ]
    },
    "gemini": {
        "api_base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env_key": "GEMINI_API_KEY",
        "default_model": "gemini-3.5-flash-lite",
        "available_models": [
            "gemini-3.5-flash-lite",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "claude-3-7-sonnet",
            "claude-3-5-sonnet",
            "claude-3-5-haiku",
            "claude-3-opus",
            "fable-5",
            "fable-5-1"
        ],
        "backend_fallback_pool": [
            "gemini-3.5-flash-lite",
            "gemini-3.6-flash",
            "gemini-3.5-flash"
        ]
    },
    "antigravity": {
        "api_base": "local://agy",
        "env_key": "",
        "default_model": "gemini-3.8-flash-low",
        "available_models": [
            "gemini-3.8-flash-low",
            "gemini-3.8-flash-medium",
            "gemini-3.8-flash-high",
            "gemini-3.7-flash-low",
            "gemini-3.7-flash-medium",
            "gemini-3.7-flash-high",
            "gemini-3.6-flash-low",
            "gemini-3.6-flash-medium",
            "gemini-3.6-flash-high",
            "gemini-3.1-pro-low",
            "gemini-3.1-pro-high",
            "claude-sonnet-4-6",
            "claude-opus-4-6-thinking",
            "gpt-oss-120b-medium"
        ],
        "backend_fallback_pool": [
            "gemini-3.8-flash-low",
            "gemini-3.7-flash-medium",
            "gemini-3.6-flash-medium",
            "gemini-3.1-pro-low",
            "claude-sonnet-4-6",
            "gpt-oss-120b-medium"
        ]
    },
    "google": {
        "api_base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env_key": "GEMINI_API_KEY",
        "default_model": "gemini-3.5-flash-lite",
        "available_models": [
            "gemini-3.5-flash-lite",
            "gemini-3.6-flash",
            "gemini-3.5-flash"
        ],
        "backend_fallback_pool": [
            "gemini-3.5-flash-lite",
            "gemini-3.6-flash",
            "gemini-3.5-flash"
        ]
    },
    "groq": {
        "api_base": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "default_model": "openai/gpt-oss-120b",
        "available_models": [
            "openai/gpt-oss-120b",
            "claude-3-7-sonnet",
            "claude-3-5-sonnet",
            "claude-3-5-haiku",
            "claude-3-opus",
            "fable-5",
            "fable-5-1"
        ],
        "backend_fallback_pool": [
            "openai/gpt-oss-120b",
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-20b",
            "qwen/qwen3.6-27b"
        ]
    },
    "openrouter": {
        "api_base": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "default_model": "anthropic/claude-3.5-sonnet",
        "available_models": ["anthropic/claude-3.5-sonnet", "openai/gpt-4o", "meta-llama/llama-3.1-405b-instruct"],
        "backend_fallback_pool": []
    },
    "duckduckgo": {
        "api_base": "https://duckduckgo.com/duckchat",
        "env_key": "",
        "default_model": "gpt-4o-mini",
        "available_models": ["gpt-4o-mini", "claude-3-haiku-20240307", "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "mistralai/Mixtral-8x7B-Instruct-v0.1"],
        "backend_fallback_pool": []
    },
    "pollinations": {
        "api_base": "https://text.pollinations.ai/",
        "env_key": "",
        "default_model": "openai",
        "available_models": ["openai", "mistral", "llama", "deepseek"],
        "backend_fallback_pool": []
    },
    "anthropic": {
        "api_base": "https://api.anthropic.com/v1",
        "env_key": "ANTHROPIC_API_KEY",
        "default_model": "claude-3-7-sonnet",
        "available_models": [
            "claude-3-7-sonnet",
            "claude-3-5-sonnet",
            "claude-3-5-haiku",
            "claude-3-opus",
            "fable-5",
            "fable-5-1"
        ],
        "backend_fallback_pool": [
            "claude-3-7-sonnet-20250219",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022"
        ]
    }
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "provider": "omniroute",
    "model": "auto/fast",
    "effort": "low",
    "ask_before_editing": True,
    "auto_confirm": False,
    "dangerously_skip_permissions": False,
    "thinking_enabled": False,
    "think_on_small_prompts": False,
    "smart_thinking": True,
    "max_tokens": 4096,
    "temperature": 0.2,
    "theme": "claude",
    "font_style": "modern",
    "deep_research": False,
    "save_code_only": False,
    "spinner": "crt_bar",
    "keys": {
        "gemini": os.getenv("GEMINI_API_KEY", ""),
        "google": os.getenv("GOOGLE_API_KEY", ""),
        "openrouter": os.getenv("OPENROUTER_API_KEY", ""),
        "groq": os.getenv("GROQ_API_KEY", ""),
        "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
        "openai": os.getenv("OPENAI_API_KEY", ""),
        "ollama": "ollama"
    },
    "custom_endpoints": {}
}


RATE_LIMIT_FILE = CONFIG_DIR / "rate_limit.json"

# Local editable usage total file where user can manually adjust total usage/credits for this computer
def get_editable_usage_file(workspace_dir: Optional[str] = None) -> Path:
    """Find or return path to the user-editable usage total file."""
    candidates = []
    if workspace_dir:
        candidates.append(Path(workspace_dir) / "usage_total.json")
    candidates.extend([
        Path("/home/feds/Projects/claude-code-replica/usage_total.json"),
        Path.cwd() / "usage_total.json",
        CONFIG_DIR / "usage_total.json"
    ])
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


class ConfigManager:
    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        self.config_file = CONFIG_FILE
        self.config = self.load_config()
        self._sync_env_keys()
        self.prompts_used, self.tier_start_time = self._load_rate_limit_state()

    def _load_rate_limit_state(self) -> Tuple[int, float]:
        if RATE_LIMIT_FILE.exists():
            try:
                with open(RATE_LIMIT_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    start_t = data.get("tier_start_time", time.time())
                    used = data.get("prompts_used", 0)
                    if time.time() - start_t > 3600:
                        return (0, time.time())
                    return (used, start_t)
            except Exception:
                pass
        return (0, time.time())

    def _save_rate_limit_state(self):
        try:
            with open(RATE_LIMIT_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "tier_start_time": self.tier_start_time,
                    "prompts_used": self.prompts_used
                }, f)
        except Exception:
            pass

    def load_config(self) -> Dict[str, Any]:
        cfg = DEFAULT_CONFIG.copy()
        is_first_run = not CONFIG_FILE.exists()

        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        if isinstance(v, dict) and k in cfg and isinstance(cfg[k], dict):
                            cfg[k].update(v)
                        else:
                            cfg[k] = v
            except Exception:
                pass
        
        # Load fallback keys from feds home config if exists
        feds_cfg_path = Path.home() / "config.json"
        if feds_cfg_path.exists():
            try:
                with open(feds_cfg_path, "r", encoding="utf-8") as f:
                    fc = json.load(f)
                    if "groq_api_key" in fc and fc["groq_api_key"]:
                        cfg["keys"]["groq"] = fc["groq_api_key"]
            except Exception:
                pass

        is_interactive = sys.stdout.isatty() and not os.environ.get("CI") and "unittest" not in sys.modules
        if (is_first_run or not cfg.get("user_name")) and is_interactive:
            print("\n" + "="*50)
            print("Welcome to Claude Code (Coder Replica)!")
            print("One-time Setup:")
            user_name = input("Enter your name: ").strip()
            if user_name:
                cfg["user_name"] = user_name
            
            groq_keys = input("Enter Groq API Keys (comma separated, or press enter to skip): ").strip()
            if groq_keys:
                cfg["keys"]["groq"] = groq_keys
            print("Setup Complete! Enjoy agentic coding.")
            print("="*50 + "\n")

        self.save_config(cfg)
        return cfg

    def _sync_env_keys(self):
        for prov, info in DEFAULT_PROVIDERS.items():
            env_var = info.get("env_key")
            if env_var and os.environ.get(env_var):
                self.config["keys"][prov] = os.environ[env_var]

    def save_config(self, cfg: Optional[Dict[str, Any]] = None):
        if cfg:
            self.config = cfg
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2)

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def set(self, key: str, value: Any):
        self.config[key] = value
        self.save_config()

    def get_api_key(self, provider: Optional[str] = None) -> str:
        provider = provider or self.config.get("provider", "groq")
        env_var = DEFAULT_PROVIDERS.get(provider, {}).get("env_key")
        if env_var and os.environ.get(env_var):
            return os.environ[env_var]
        return self.config.get("keys", {}).get(provider, "")

    def set_api_key(self, provider: str, key: str):
        if "keys" not in self.config:
            self.config["keys"] = {}
        self.config["keys"][provider] = key
        self.save_config()

    def resolve_backend_model(self, model_name: Optional[str] = None, provider: Optional[str] = None) -> str:
        """Resolve a friendly Claude name (e.g. claude-3-7-sonnet) to the backend model ID."""
        prov = provider or self.config.get("provider", "groq")
        m_name = (model_name or self.config.get("model", "claude-3-7-sonnet")).lower()
        
        prov_map = CLAUDE_MODEL_MAPPINGS.get(prov, {})
        if m_name in prov_map:
            return prov_map[m_name]["backend_id"]
        
        alias_map = {
            "sonnet": "claude-3-7-sonnet",
            "3.7": "claude-3-7-sonnet",
            "3.7-sonnet": "claude-3-7-sonnet",
            "3.5-sonnet": "claude-3-5-sonnet",
            "haiku": "claude-3-5-haiku",
            "3.5-haiku": "claude-3-5-haiku",
            "opus": "claude-3-opus",
            "3-opus": "claude-3-opus",
            "fable": "fable-5",
            "fable 5": "fable-5",
            "fable-5": "fable-5",
            "fable5": "fable-5",
            "fable 5.1": "fable-5-1",
            "fable-5.1": "fable-5-1",
            "fable-5-1": "fable-5-1",
            "fable5.1": "fable-5-1",
            "120b": "openai/gpt-oss-120b",
            "gpt-120b": "openai/gpt-oss-120b",
            "gpt-oss-120b": "openai/gpt-oss-120b",
            "chagpt 120b oss": "openai/gpt-oss-120b",
            "chagpt": "openai/gpt-oss-120b",
            "chatgpt 120b": "openai/gpt-oss-120b",
            "chatgpt-120b": "openai/gpt-oss-120b",
            "gemini-flash-lite": "gemini-3.5-flash-lite",
            "gemini-lite": "gemini-3.5-flash-lite",
            "flash-lite": "gemini-3.5-flash-lite",
            "lite": "gemini-3.5-flash-lite",
            "cheapest": "gemini-3.5-flash-lite",
            "gemini-flash": "gemini-3.6-flash",
            "flash": "gemini-3.6-flash",
            "gemini-2.5-flash": "gemini-3.5-flash-lite",
            "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
            # Gemini 3.8 Flash
            "3.8": "gemini-3.8-flash-low",
            "3.8-flash": "gemini-3.8-flash-low",
            "flash-3.8": "gemini-3.8-flash-low",
            "gemini-3.8": "gemini-3.8-flash-low",
            "gemini 3.8": "gemini-3.8-flash-low",
            "gemini 3.8 flash": "gemini-3.8-flash-low",
            "gemini-3.8-flash": "gemini-3.8-flash-low",
            "3.8-low": "gemini-3.8-flash-low",
            "3.8-medium": "gemini-3.8-flash-medium",
            "3.8-med": "gemini-3.8-flash-medium",
            "3.8-high": "gemini-3.8-flash-high",
            # Gemini 3.7 Flash
            "3.7-flash": "gemini-3.7-flash-medium",
            "flash-3.7": "gemini-3.7-flash-medium",
            "gemini-3.7": "gemini-3.7-flash-medium",
            "gemini 3.7": "gemini-3.7-flash-medium",
            "gemini-3.7-flash": "gemini-3.7-flash-medium",
            "gemini 3.7 flash": "gemini-3.7-flash-medium",
            "3.7-low": "gemini-3.7-flash-low",
            "3.7-medium": "gemini-3.7-flash-medium",
            "3.7-med": "gemini-3.7-flash-medium",
            "3.7-high": "gemini-3.7-flash-high",
            # Gemini 3.6 Flash
            "3.6-flash": "gemini-3.6-flash-low",
            "flash-3.6": "gemini-3.6-flash-low",
            "gemini-3.6": "gemini-3.6-flash-low",
            "gemini 3.6": "gemini-3.6-flash-low",
            "gemini-3.6-flash": "gemini-3.6-flash-low",
            "gemini 3.6 flash": "gemini-3.6-flash-low",
            "3.6-low": "gemini-3.6-flash-low",
            "3.6-medium": "gemini-3.6-flash-medium",
            "3.6-med": "gemini-3.6-flash-medium",
            "3.6-high": "gemini-3.6-flash-high",
            # Gemini 3.1 Pro
            "3.1": "gemini-3.1-pro-high",
            "3.1-pro": "gemini-3.1-pro-high",
            "pro": "gemini-3.1-pro-high",
            "gemini-pro": "gemini-3.1-pro-high",
            "gemini-3.1": "gemini-3.1-pro-high",
            "gemini 3.1": "gemini-3.1-pro-high",
            "gemini-3.1-pro": "gemini-3.1-pro-high",
            "gemini 3.1 pro": "gemini-3.1-pro-high",
            "3.1-low": "gemini-3.1-pro-low",
            "3.1-high": "gemini-3.1-pro-high",
            "pro-low": "gemini-3.1-pro-low",
            "pro-high": "gemini-3.1-pro-high",
            # Claude Sonnet 4.6 & Opus 4.6
            "sonnet-4.6": "claude-sonnet-4-6",
            "sonnet-4-6": "claude-sonnet-4-6",
            "claude-4.6-sonnet": "claude-sonnet-4-6",
            "opus-4.6": "claude-opus-4-6-thinking",
            "opus-4-6": "claude-opus-4-6-thinking",
            "claude-4.6-opus": "claude-opus-4-6-thinking",
            "claude-opus-4-6": "claude-opus-4-6-thinking",
            # OmniRoute aliases
            "auto": "auto",
            "fast": "auto/fast",
            "auto-fast": "auto/fast",
            "coding": "auto/coding",
            "auto-coding": "auto/coding",
            "omni": "auto",
            "omniroute": "auto"
        }
        if m_name in alias_map and alias_map[m_name] in prov_map:
            return prov_map[alias_map[m_name]]["backend_id"]
        elif m_name in alias_map:
            return alias_map[m_name]

        return m_name

    def get_display_model_name(self, model_name: Optional[str] = None, provider: Optional[str] = None) -> str:
        """Get the polished Claude marketing name for display."""
        prov = provider or self.config.get("provider", "omniroute")
        m_name = (model_name or self.config.get("model", "auto/fast")).lower()
        
        alias_map = {
            "sonnet": "claude-3-7-sonnet",
            "3.7": "claude-3-7-sonnet",
            "3.7-sonnet": "claude-3-7-sonnet",
            "3.5-sonnet": "claude-3-5-sonnet",
            "haiku": "claude-3-5-haiku",
            "3.5-haiku": "claude-3-5-haiku",
            "opus": "claude-3-opus",
            "3-opus": "claude-3-opus",
            "fable": "fable-5",
            "fable 5": "fable-5",
            "fable-5": "fable-5",
            "fable5": "fable-5",
            "fable 5.1": "fable-5-1",
            "fable-5.1": "fable-5-1",
            "fable-5-1": "fable-5-1",
            "fable5.1": "fable-5-1",
            "120b": "openai/gpt-oss-120b",
            "gpt-120b": "openai/gpt-oss-120b",
            "gpt-oss-120b": "openai/gpt-oss-120b",
            "chagpt 120b oss": "openai/gpt-oss-120b",
            "chagpt": "openai/gpt-oss-120b",
            "chatgpt 120b": "openai/gpt-oss-120b",
            "chatgpt-120b": "openai/gpt-oss-120b",
            "gemini-flash-lite": "gemini-3.5-flash-lite",
            "gemini-lite": "gemini-3.5-flash-lite",
            "flash-lite": "gemini-3.5-flash-lite",
            "lite": "gemini-3.5-flash-lite",
            "cheapest": "gemini-3.5-flash-lite",
            "gemini-flash": "gemini-3.6-flash",
            "flash": "gemini-3.6-flash",
            "gemini-2.5-flash": "gemini-3.5-flash-lite",
            "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
            # Gemini 3.8 Flash
            "3.8": "gemini-3.8-flash-low",
            "3.8-flash": "gemini-3.8-flash-low",
            "flash-3.8": "gemini-3.8-flash-low",
            "gemini-3.8": "gemini-3.8-flash-low",
            "gemini 3.8": "gemini-3.8-flash-low",
            "gemini 3.8 flash": "gemini-3.8-flash-low",
            "gemini-3.8-flash": "gemini-3.8-flash-low",
            "3.8-low": "gemini-3.8-flash-low",
            "3.8-medium": "gemini-3.8-flash-medium",
            "3.8-med": "gemini-3.8-flash-medium",
            "3.8-high": "gemini-3.8-flash-high",
            # Gemini 3.7 Flash
            "3.7-flash": "gemini-3.7-flash-medium",
            "flash-3.7": "gemini-3.7-flash-medium",
            "gemini-3.7": "gemini-3.7-flash-medium",
            "gemini 3.7": "gemini-3.7-flash-medium",
            "gemini-3.7-flash": "gemini-3.7-flash-medium",
            "gemini 3.7 flash": "gemini-3.7-flash-medium",
            "3.7-low": "gemini-3.7-flash-low",
            "3.7-medium": "gemini-3.7-flash-medium",
            "3.7-med": "gemini-3.7-flash-medium",
            "3.7-high": "gemini-3.7-flash-high",
            # Gemini 3.6 Flash
            "3.6-flash": "gemini-3.6-flash-low",
            "flash-3.6": "gemini-3.6-flash-low",
            "gemini-3.6": "gemini-3.6-flash-low",
            "gemini 3.6": "gemini-3.6-flash-low",
            "gemini-3.6-flash": "gemini-3.6-flash-low",
            "gemini 3.6 flash": "gemini-3.6-flash-low",
            "3.6-low": "gemini-3.6-flash-low",
            "3.6-medium": "gemini-3.6-flash-medium",
            "3.6-med": "gemini-3.6-flash-medium",
            "3.6-high": "gemini-3.6-flash-high",
            # Gemini 3.1 Pro
            "3.1": "gemini-3.1-pro-high",
            "3.1-pro": "gemini-3.1-pro-high",
            "pro": "gemini-3.1-pro-high",
            "gemini-pro": "gemini-3.1-pro-high",
            "gemini-3.1": "gemini-3.1-pro-high",
            "gemini 3.1": "gemini-3.1-pro-high",
            "gemini-3.1-pro": "gemini-3.1-pro-high",
            "gemini 3.1 pro": "gemini-3.1-pro-high",
            "3.1-low": "gemini-3.1-pro-low",
            "3.1-high": "gemini-3.1-pro-high",
            "pro-low": "gemini-3.1-pro-low",
            "pro-high": "gemini-3.1-pro-high",
            # Claude Sonnet 4.6 & Opus 4.6
            "sonnet-4.6": "claude-sonnet-4-6",
            "sonnet-4-6": "claude-sonnet-4-6",
            "claude-4.6-sonnet": "claude-sonnet-4-6",
            "opus-4.6": "claude-opus-4-6-thinking",
            "opus-4-6": "claude-opus-4-6-thinking",
            "claude-4.6-opus": "claude-opus-4-6-thinking",
            "claude-opus-4-6": "claude-opus-4-6-thinking",
            # OmniRoute aliases
            "auto": "auto",
            "fast": "auto/fast",
            "auto-fast": "auto/fast",
            "coding": "auto/coding",
            "auto-coding": "auto/coding",
            "omni": "auto",
            "omniroute": "auto"
        }
        if m_name in alias_map:
            m_name = alias_map[m_name]

        prov_map = CLAUDE_MODEL_MAPPINGS.get(prov, {})
        if m_name in prov_map:
            return prov_map[m_name]["display_name"]
            
        for k, v in prov_map.items():
            if v["backend_id"] == m_name:
                return v["display_name"]

        return m_name.split("/")[-1].replace("-", " ").title()

    # Rate Limit & Effort Tracking
    def get_effort_info(self) -> Dict[str, Any]:
        eff = self.config.get("effort", "normal")
        return EFFORT_TIERS.get(eff, EFFORT_TIERS["normal"])

    def set_effort(self, tier: str) -> bool:
        """Set effort tier, updating reasoning depth and limits."""
        tier_clean = tier.lower().strip().replace("-", " ").replace("_", " ")
        if tier_clean in EFFORT_TIERS:
            info = EFFORT_TIERS[tier_clean]
            self.config["effort"] = tier_clean
            self.config["temperature"] = info["temperature"]
            self.config["max_tokens"] = info["max_tokens"]
            self.save_config()
            return True
        return False

    def check_rate_limit(self) -> Tuple[bool, int, int]:
        """Returns (is_allowed, remaining_prompts, limit)."""
        info = self.get_effort_info()
        limit = info["limit"]
        
        # Reset window after 1 hour
        if time.time() - self.tier_start_time > 3600:
            self.prompts_used = 0
            self.tier_start_time = time.time()
            self._save_rate_limit_state()

        remaining = max(0, limit - self.prompts_used)
        return (remaining > 0, remaining, limit)

    def record_prompt_usage(self):
        self.prompts_used += 1
        self._save_rate_limit_state()

    # Lifetime Telemetry & Credits Tracking for /total
    def record_telemetry(self, input_tokens: int, output_tokens: int, duration_s: float):
        """Record usage statistics into persistent total_usage.json."""
        usage_file = CONFIG_DIR / "total_usage.json"
        data = {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "total_credits_used": 0.0,
            "total_turns": 0,
            "total_duration_s": 0.0,
            "total_sessions": 1
        }
        if usage_file.exists():
            try:
                with open(usage_file, "r", encoding="utf-8") as f:
                    data.update(json.load(f))
            except Exception:
                pass

        total_toks = input_tokens + output_tokens
        data["total_input_tokens"] += input_tokens
        data["total_output_tokens"] += output_tokens
        data["total_tokens"] += total_toks
        # 1 credit per 1,000 tokens processed
        data["total_credits_used"] = round(data["total_tokens"] / 1000.0, 3)
        data["total_turns"] += 1
        data["total_duration_s"] = round(data["total_duration_s"] + duration_s, 2)

        try:
            with open(usage_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

        # Also sync or initialize editable file so user custom edits are preserved and updated
        edit_file = get_editable_usage_file()
        if edit_file.exists():
            try:
                with open(edit_file, "r", encoding="utf-8") as f:
                    u_data = json.load(f)
                if isinstance(u_data, dict):
                    u_data["total_tokens"] = u_data.get("total_tokens", 0) + total_toks
                    u_data["total_input_tokens"] = u_data.get("total_input_tokens", 0) + input_tokens
                    u_data["total_output_tokens"] = u_data.get("total_output_tokens", 0) + output_tokens
                    u_data["total_credits_used"] = round(u_data["total_tokens"] / 1000.0, 3)
                    u_data["total_turns"] = u_data.get("total_turns", 0) + 1
                    u_data["total_duration_s"] = round(u_data.get("total_duration_s", 0.0) + duration_s, 2)
                    with open(edit_file, "w", encoding="utf-8") as f:
                        json.dump(u_data, f, indent=2)
            except Exception:
                pass
        else:
            try:
                with open(edit_file, "w", encoding="utf-8") as f:
                    json.dump({
                        "total_credits_used": data["total_credits_used"],
                        "total_tokens": data["total_tokens"],
                        "total_input_tokens": data["total_input_tokens"],
                        "total_output_tokens": data["total_output_tokens"],
                        "total_turns": data["total_turns"],
                        "hourly_limit": 40,
                        "notes": "Edit this file to customize your local usage totals and credits for this machine."
                    }, f, indent=2)
            except Exception:
                pass

        # Record daily usage for /usage heatmap
        daily_file = CONFIG_DIR / "daily_usage.json"
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        daily_data = {}
        if daily_file.exists():
            try:
                with open(daily_file, "r") as f:
                    daily_data = json.load(f)
            except Exception:
                pass
        daily_data[today] = daily_data.get(today, 0) + total_toks
        try:
            with open(daily_file, "w") as f:
                json.dump(daily_data, f)
        except Exception:
            pass

    def get_total_telemetry(self) -> Dict[str, Any]:
        """Fetch lifetime usage metrics, prioritizing user-edited usage_total.json."""
        edit_file = get_editable_usage_file()
        if edit_file.exists():
            try:
                with open(edit_file, "r", encoding="utf-8") as f:
                    u_data = json.load(f)
                    if isinstance(u_data, dict) and ("total_tokens" in u_data or "total_credits_used" in u_data):
                        toks = int(u_data.get("total_tokens", 0))
                        creds = float(u_data.get("total_credits_used", round(toks / 1000.0, 3)))
                        in_toks = int(u_data.get("total_input_tokens", int(toks * 0.6)))
                        out_toks = int(u_data.get("total_output_tokens", int(toks * 0.4)))
                        turns = int(u_data.get("total_turns", 0))
                        dur = float(u_data.get("total_duration_s", 0.0))
                        return {
                            "total_input_tokens": in_toks,
                            "total_output_tokens": out_toks,
                            "total_tokens": toks,
                            "total_credits_used": creds,
                            "total_turns": turns,
                            "total_duration_s": dur,
                            "total_sessions": 1,
                            "is_user_custom": True,
                            "source_file": str(edit_file)
                        }
            except Exception:
                pass

        usage_file = CONFIG_DIR / "total_usage.json"
        data = {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "total_credits_used": 0.0,
            "total_turns": 0,
            "total_duration_s": 0.0,
            "total_sessions": 1,
            "is_user_custom": False,
            "source_file": str(usage_file)
        }
        if usage_file.exists():
            try:
                with open(usage_file, "r", encoding="utf-8") as f:
                    data.update(json.load(f))
            except Exception:
                pass
        return data

    # Real-Time Session Tracking for Usage Shower JS
    def init_session_usage(self, session_id: str, model: str, provider: str, workspace_dir: Optional[str] = None):
        """Initialize real-time session tracking file for the live Usage Shower JS app."""
        usage_file = CONFIG_DIR / "current_session_usage.json"
        now = time.time()
        data = {
            "session_id": session_id,
            "start_time": now,
            "start_time_iso": datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
            "active_model": model,
            "provider": provider,
            "workspace_dir": str(workspace_dir or os.getcwd()),
            "agent_state": "idle",
            "last_activity": now,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "credits_used": 0.0,
            "duration_s": 0.0,
            "turns_count": 0,
            "turns": []
        }
        try:
            with open(usage_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def set_agent_state(self, state: str):
        """Update live agent state ('loading', 'answering', 'idle') for Clawd mascot animations."""
        usage_file = CONFIG_DIR / "current_session_usage.json"
        data = self.get_session_usage()
        data["agent_state"] = state
        data["last_activity"] = time.time()
        try:
            with open(usage_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def record_turn_to_session(self, turn_data: Dict[str, Any]):
        """Record turn metrics into current_session_usage.json for live updating."""
        usage_file = CONFIG_DIR / "current_session_usage.json"
        data = self.get_session_usage()
        in_tok = turn_data.get("input_tokens", 0)
        out_tok = turn_data.get("output_tokens", 0)
        dur = turn_data.get("duration", 0.0)

        data["agent_state"] = "idle"
        data["last_activity"] = time.time()
        data["input_tokens"] += in_tok
        data["output_tokens"] += out_tok
        data["total_tokens"] += (in_tok + out_tok)
        data["credits_used"] = round(data["total_tokens"] / 1000.0, 3)
        data["duration_s"] = round(data.get("duration_s", 0.0) + dur, 2)
        data["turns_count"] = data.get("turns_count", 0) + 1
        if "turns" not in data or not isinstance(data["turns"], list):
            data["turns"] = []
        data["turns"].append(turn_data)

        try:
            with open(usage_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def get_session_usage(self) -> Dict[str, Any]:
        """Fetch current session usage data."""
        usage_file = CONFIG_DIR / "current_session_usage.json"
        if usage_file.exists():
            try:
                with open(usage_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        now = time.time()
        return {
            "session_id": f"sess_{int(now)}",
            "start_time": now,
            "start_time_iso": datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
            "active_model": self.get("model", "claude-3-7-sonnet"),
            "provider": self.get("provider", "groq"),
            "workspace_dir": os.getcwd(),
            "agent_state": "idle",
            "last_activity": now,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "credits_used": 0.0,
            "duration_s": 0.0,
            "turns_count": 0,
            "turns": []
        }
