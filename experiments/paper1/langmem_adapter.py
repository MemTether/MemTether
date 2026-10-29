#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, os, json

def create_langmem_memory():
    for k in ["OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE"]:
        os.environ.pop(k, None)
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model="cn:glm-5.3-flash", base_url="http://127.0.0.1:8814/v1", api_key="dummy", temperature=0.1)
    from langmem import create_memory_store_manager
    return create_memory_store_manager(llm)

def langmem_add_sessions(manager, sessions, user_id):
    messages = []
    for session in sessions:
        for turn in session:
            content = turn.get("content", "")
            if content and len(content) > 50:
                messages.append({"role": turn.get("role", "user"), "content": content[:2000]})
    if messages:
        manager(messages)

def langmem_search(store, query, user_id, top_k=10):
    try:
        results = store.search([user_id, "memories"], query=query, limit=top_k)
        return results
    except Exception:
        return []
