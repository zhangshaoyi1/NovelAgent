import time
from agent.core.llm_client import LLMClient, LLMConfig

cfg = LLMClient._load_from_env()
print("provider:", cfg.provider)
print("model:", cfg.model)
print("base_url:", cfg.base_url)
print("timeout:", cfg.timeout)
print("enable_thinking(config):", cfg.enable_thinking)

client = LLMClient(config=cfg)
t = time.time()
try:
    resp = client.chat(
        messages=[{"role": "user", "content": "用一句话证明你能正常回复，中文回答。"}],
        max_tokens=200,
        temperature=0.7,
    )
    dt = time.time() - t
    print("elapsed: %.1fs" % dt)
    print("content:", repr(resp.text))
    print("usage:", resp.usage)
    if resp.text and len(resp.text) > 5:
        print("SMOKE_OK")
    else:
        print("SMOKE_EMPTY_CONTENT")
except Exception as e:
    print("SMOKE_ERR after %.1fs:" % (time.time() - t), type(e).__name__, str(e)[:300])
