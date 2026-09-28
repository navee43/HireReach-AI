"""Hugging Face Inference Providers - OpenAI-compatible router.

Model names look like "meta-llama/Llama-3.1-8B-Instruct" (optionally with
":fastest" or ":cheapest" on the end). Token from https://huggingface.co/settings/tokens
"""
from llm.openai_compatible import OpenAICompatibleProvider


class HuggingFaceProvider(OpenAICompatibleProvider):
    provider_name = "huggingface"
    DEFAULT_BASE_URL = "https://router.huggingface.co/v1"
