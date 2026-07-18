"""Configures the LLM model for the planner agent."""

import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model  # noqa: F401
from langchain_ollama import ChatOllama

load_dotenv(".env.local")

os.environ["GOOGLE_API_KEY"] = os.getenv("MODEL_API_KEY")

model = ChatOllama(model="ornith:latest", reasoning=False)
# model = init_chat_model("google_genai:gemini-3.1-flash-lite")
