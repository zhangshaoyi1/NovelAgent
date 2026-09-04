"""架构测试：secrets 模块正确性"""

import os

from llmagent.gateway.secrets import PROVIDER_ENV_WHITELIST, load_credentials


class TestSecrets:
    def test_whitelist_only_llmagent_prefix(self):
        for name in PROVIDER_ENV_WHITELIST:
            assert name.startswith("LLMAGENT_GATEWAY_"), f"{name} 不符合命名规范"

    def test_load_credentials_only_whitelisted(self, monkeypatch):
        monkeypatch.setenv("LLMAGENT_GATEWAY_OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("NONCE_ENV", "should-not-appear")
        creds = load_credentials()
        assert "LLMAGENT_GATEWAY_OPENAI_API_KEY" in creds
        assert "NONCE_ENV" not in creds
        assert creds["LLMAGENT_GATEWAY_OPENAI_API_KEY"] == "sk-test"