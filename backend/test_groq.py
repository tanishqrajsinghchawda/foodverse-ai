import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

response = client.chat.completions.create(
        model="qwen/qwen3.8-27b",
    messages=[{"role": "user", "content": "Name 3 healthy Indian breakfast dishes in one line."}],
)

print(response.choices[0].message.content)