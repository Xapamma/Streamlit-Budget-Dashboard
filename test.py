import ollama

response = ollama.chat(
    model="phi3",
    messages=[
        {"role": "user", "content": "Say hello in one word"}
    ]
)

print(response["message"]["content"])