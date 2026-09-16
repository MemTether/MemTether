# -*- coding: utf-8 -*-
"""
mem0_config.py — Mem0 接入配置（阶段2，2026-09-13）

Mem0 需要三样东西：LLM（事实提取+冲突判断）、embedder（向量化）、vector_store（存储）。
本机约束：无 Docker、费用敏感、有 deepseek/astra 通道、有 VPN。

策略（astra 裁决）：
  - LLM：用本机已有通道（gptx_astra 最强 / deepseek_official 便宜），不额外花钱用 OpenAI。
  - embedder：优先本地 embedding；若无本地模型，用 deepseek/开源免费 embedding。
  - vector_store：ChromaDB 本地（纯目录，无需 Docker）。

注意：Mem0 的 LLM provider 支持 openai 兼容接口（deepseek/gptx 都兼容 openai 协议），
用 provider="openai" + openai_base_url 指向本机通道即可复用。
"""
import os
import sys

# 通道配置（从 cred_env 解析）
sys.path.insert(0, r'<AUDIT>')
try:
    import cred_env
    cred_env.env()
except Exception:
    pass

HUB = r'<HUB>'

# 主 LLM：gptx_astra（最强，事实提取质量最高）
GPTX_BASE = 'https://api.gptx.cc/v1'
GPTX_KEY = os.environ.get('GPTX_ASTRA_KEY', '')

# 备用 LLM：deepseek_official（便宜）
DS_KEY = os.environ.get('DEEPSEEK_API_KEY', os.environ.get('DEEPSEEK_OFFICIAL_KEY', ''))

MEM0_CONFIG = {
    'llm': {
        'provider': 'openai',
        'config': {
            'model': 'gpt-6-astra',
            'api_key': GPTX_KEY,
            'openai_base_url': GPTX_BASE,
            'temperature': 0.1,
            'max_tokens': 2000,
        },
    },
    # embedder 用智谱 embedding-3（免费，2048 维，中文友好）
    'embedder': {
        'provider': 'openai',
        'config': {
            'model': 'embedding-3',
            'api_key': os.environ.get('ZHIPU_KEY', ''),
            'openai_base_url': 'https://open.bigmodel.cn/api/paas/v4',
            'embedding_dims': 2048,
        },
    },
    'vector_store': {
        'provider': 'chroma',
        'config': {
            'collection_name': 'memory_hub',
            'path': os.path.join(HUB, 'mem0_store'),
        },
    },
    'version': 'v1.1',
}


def get_mem0():
    """返回 Memory 实例（懒加载）。"""
    from mem0 import Memory
    return Memory.from_config(MEM0_CONFIG)
