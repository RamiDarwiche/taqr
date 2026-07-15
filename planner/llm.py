import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

load_dotenv()

os.environ["MODEL_API_KEY"] = os.environ["MODEL_API_KEY"]
model = init_chat_model("google_genai:gemini-3-flash-preview")
