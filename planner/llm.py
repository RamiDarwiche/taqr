from langchain.chat_models import init_chat_model
import os

from dotenv import load_dotenv
from langchain_ollama import ChatOllama

load_dotenv(".env.local")

os.environ["GOOGLE_API_KEY"] = os.getenv("MODEL_API_KEY")

# model = ChatOllama(model="ornith:latest", reasoning=False)
model = init_chat_model("google_genai:gemini-3.1-flash-lite")
