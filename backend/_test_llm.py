import settings_store
settings_store.set_settings({
  "DEEPSEEK_API_KEY": "",
  "DEEPSEEK_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "DEEPSEEK_MODEL": "deepseek-v3",
})
settings_store.apply_runtime_settings()
import config, llm
print("base", llm.DEEPSEEK_BASE_URL)
print("model", llm.DEEPSEEK_MODEL)
print("LLM_ENABLED", llm.LLM_ENABLED)
print("key_len", len(llm.DEEPSEEK_API_KEY or ""))
print("key_starts_sk", (llm.DEEPSEEK_API_KEY or "").startswith("sk-"))
print("same_as_dash", llm.DEEPSEEK_API_KEY == config.DASHSCOPE_API_KEY and bool(llm.DEEPSEEK_API_KEY))
ans = llm.chat_sync([{"role":"user","content":"请只回复两个字:正常"}], temperature=0.1, timeout=45)
print("has_Mock", "Mock" in ans)
print("has_401", "401" in ans)
print("preview", ans[:120].replace("\n"," | "))
