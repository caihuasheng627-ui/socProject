"""LLM model ID normalization and failure messaging."""

from llm_routing import normalize_llm_model


BAILIAN = "https://dashscope.aliyuncs.com/compatible-mode/v1"
CODING = "https://coding.dashscope.aliyuncs.com/v1"
OFFICIAL = "https://api.deepseek.com"


def test_normalize_bailian_aliases():
    assert normalize_llm_model("deepseek-chat", BAILIAN) == "deepseek-v3"
    assert normalize_llm_model("DeepSeek-V3", BAILIAN) == "deepseek-v3"
    assert normalize_llm_model("deepseek", BAILIAN) == "deepseek-v3"
    assert normalize_llm_model("qwen-plus", BAILIAN) == "qwen-plus"
    assert normalize_llm_model("deepseek-reasoner", BAILIAN) == "deepseek-r1"


def test_normalize_glm_typos():
    assert normalize_llm_model("glm5", BAILIAN) == "glm-5"
    assert normalize_llm_model("GLM5", BAILIAN) == "glm-5"
    assert normalize_llm_model("glm-5", BAILIAN) == "glm-5"
    assert normalize_llm_model("glm5.1", BAILIAN) == "glm-5.1"
    assert normalize_llm_model("glm5.2", CODING) == "glm-5.2"
    assert normalize_llm_model("glm4.7", BAILIAN) == "glm-4.7"
    assert normalize_llm_model("glm-5-0", BAILIAN) == "glm-5"


def test_normalize_official_aliases():
    assert normalize_llm_model("deepseek-v3", OFFICIAL) == "deepseek-chat"
    assert normalize_llm_model("deepseek-chat", OFFICIAL) == "deepseek-chat"
    assert normalize_llm_model("deepseek", OFFICIAL) == "deepseek-chat"
