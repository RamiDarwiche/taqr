import os

from dotenv import load_dotenv
from langchain_ollama import ChatOllama

load_dotenv()

os.environ["MODEL_API_KEY"] = os.environ["MODEL_API_KEY"]

# reasoning=False is required for reliable tool calling with Qwen3-class models on
# Ollama: thinking+tools often yields empty content and no tool_calls.
# Note: ChatOllama also ignores tool_choice — do not rely on it to force calls.
model = ChatOllama(model="qwen3.5:9b", reasoning=False)
